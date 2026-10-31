"""Account failover and concurrency orchestration."""

from __future__ import annotations

import asyncio
import time

from aistudio_api.api.state import runtime_state
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("orchestrator")
MAX_RETRIES = 5

_switch_lock: asyncio.Lock | None = None
_switch_lock_loop: asyncio.AbstractEventLoop | None = None
_last_switch_time: float = 0.0


def _get_switch_lock() -> asyncio.Lock:
    global _switch_lock, _switch_lock_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _switch_lock is None or _switch_lock_loop is not loop:
        _switch_lock = asyncio.Lock()
        _switch_lock_loop = loop
    return _switch_lock


async def try_switch_account(
    model: str | None = None,
    failed_account_id: str | None = None,
    required_tiers: tuple[str, ...] | list[str] | None = None,
    *,
    is_session_expired: bool = False,
) -> bool:
    """尝试切换到下一个对目标 model 可用的账号。防止并发级联切号。"""
    global _last_switch_time
    entry_time = time.time()

    if model and not required_tiers:
        from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

        tiers = model_discovery.get_required_tiers(model)
        if tiers:
            required_tiers = tuple(tiers)

    # 乐观快速预检：若无需记录永久失效/短时隔离，且当前活跃账号已被并发请求切换至健康状态，直接免锁复用
    if failed_account_id and not is_session_expired:
        rotator = runtime_state.rotator
        account_service = runtime_state.account_service
        if rotator is not None and account_service is not None:
            cur_active = account_service.get_active_account()
            if (
                cur_active
                and (
                    cur_active.id != failed_account_id
                    or _last_switch_time >= entry_time
                )
                and (not required_tiers or cur_active.tier in required_tiers)
                and rotator.is_account_available(cur_active.id, model=model)
            ):
                return True

    async with _get_switch_lock():
        rotator = runtime_state.rotator
        account_service = runtime_state.account_service
        client = runtime_state.client

        if (
            rotator is None
            or account_service is None
            or client is None
            or client.session is None
        ):
            return False

        current_active = account_service.get_active_account()
        current_id = current_active.id if current_active else None

        # 双重检查 1：如果已有并发协程在 entry_time 之后完成切号或在位自愈刷新，且当前账号对该模型可用，则直接复用
        if (
            _last_switch_time >= entry_time
            and current_id
            and (not is_session_expired or current_id != failed_account_id)
            and (
                not required_tiers
                or (current_active and current_active.tier in required_tiers)
            )
            and rotator.is_account_available(current_id, model=model)
        ):
            logger.info(
                "并发请求已完成切号，复用当前账号: account=%s, model=%s",
                current_id,
                model,
            )
            return True

        # 遇到登录态失效时，永久禁用该账号并拒绝原地重试
        if failed_account_id and is_session_expired:
            rotator.record_session_expired(failed_account_id, model=model)

        # 双重检查 2：如果已经由并发协程切换到了新账号，且新账号对当前 model 可用，则直接复用
        if (
            failed_account_id
            and current_id
            and current_id != failed_account_id
            and (
                not required_tiers
                or (current_active and current_active.tier in required_tiers)
            )
            and rotator.is_account_available(current_id, model=model)
        ):
            logger.info(
                "并发请求已完成切号，复用当前账号: account=%s, model=%s",
                current_id,
                model,
            )
            return True

        next_account = await rotator.get_next_account(
            model=model,
            current_account_id=current_id,
            failed_account_id=failed_account_id,
            required_tiers=required_tiers,
            is_session_expired=is_session_expired,
        )
        if next_account is None:
            if is_session_expired:
                logger.error(
                    "账号 %s 登录态失效且无其他可用账号，停止重试",
                    failed_account_id,
                )
            else:
                logger.warning(
                    "未找到可用备用账号: model=%s, failed_account=%s",
                    model,
                    failed_account_id,
                )
            return False

        if current_id is None or next_account.id != current_id:
            result = await account_service.activate_account(
                next_account.id,
                client.session,
            )
            if result is not None:
                rotator.clear_cooldown(next_account.id, model=model)
                _last_switch_time = time.time()
                logger.info(
                    "账号切换成功: %s -> %s, model=%s",
                    current_id,
                    next_account.id,
                    model,
                )
            return result is not None
        # 登录态失效严禁在原账号原地重试
        if is_session_expired and (
            failed_account_id == current_id or next_account.id == failed_account_id
        ):
            logger.error("账号 %s 登录态已失效，禁止在原账号重试", failed_account_id)
            return False

        # 单账号模式或所有其他账号均不可用时（非登录态失效，如偶发 403 鉴权波动），如果指定了 failed_account_id，强制刷新当前会话与 BotGuard
        if failed_account_id and failed_account_id == current_id:
            logger.info(
                "无其他可用备用账号，重置当前账号会话: account=%s",
                current_id,
            )
            client.clear_templates()
            result = await account_service.activate_account(
                current_id,
                client.session,
            )
            if result is not None:
                rotator.clear_cooldown(current_id, model=model)
                _last_switch_time = time.time()
            return result is not None
        return bool(
            current_id
            and (
                not required_tiers
                or (current_active and current_active.tier in required_tiers)
            )
            and rotator.is_account_available(current_id, model=model)
        )


async def ensure_active_account(attempt: int, model: str | None = None) -> None:
    """确保在初次尝试时存在活跃账号，且若请求付费模型而当前账号权益不足时主动抢切。"""
    if attempt != 0:
        return
    account_svc = runtime_state.account_service
    rotator = runtime_state.rotator
    current = account_svc.get_active_account() if account_svc else None

    # 核心拦截：检测目标模型是否为 Paid 模型且当前账号层级不满足要求
    if model:
        from fastapi import HTTPException

        from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

        if model_discovery.is_paid_model(model):
            req_tier = model_discovery.get_required_tier(model)
            req_tiers = model_discovery.get_required_tiers(model)
            allowed_tiers = (
                set(req_tiers)
                if req_tiers
                else (
                    {"Pro", "Ultra", "Ultra_20X"}
                    if req_tier == "Pro"
                    else {"Ultra", "Ultra_20X"}
                )
            )
            cur_tier = (current.tier if current else None) or "Free"
            if not current or cur_tier not in allowed_tiers:
                logger.info(
                    "模型 %s 需要 %s 权益，当前账号 %s 为 %s，切换至付费账号",
                    model,
                    req_tier,
                    current.id if current else "none",
                    cur_tier,
                )
                switched = await try_switch_account(
                    model=model,
                    required_tiers=tuple(req_tiers)
                    if req_tiers
                    else tuple(allowed_tiers),
                )
                if not switched:
                    logger.warning(
                        "请求模型 %s 需要 %s 付费权益，但账号池中无就绪的可用账号",
                        model,
                        req_tier,
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "message": f"模型 '{model}' 需要 Google AI Studio {req_tier} 订阅，当前账号池中无就绪的付费账号。",
                            "type": "permission_denied",
                        },
                    )
                return

    if not current:
        await try_switch_account(model=model)
    elif (
        rotator and model and not rotator.is_account_available(current.id, model=model)
    ):
        await try_switch_account(model=model, failed_account_id=current.id)


def record_rotator_event(
    event: str,
    model: str | None = None,
    usage: dict[str, object] | None = None,
) -> None:
    """记录调度器事件（成功、限流、错误、鉴权异常）。"""
    rotator = runtime_state.rotator
    account_service = runtime_state.account_service
    account = account_service.get_active_account() if account_service else None
    if not rotator or account is None:
        return
    if event == "success":
        rotator.record_success(account.id, model=model, usage=usage)
    elif event == "rate_limited":
        rotator.record_rate_limited(account.id, model=model)
    elif event == "auth_error":
        rotator.record_auth_error(account.id, model=model)
    elif event == "error":
        rotator.record_error(account.id, model=model)


__all__ = [
    "MAX_RETRIES",
    "ensure_active_account",
    "record_rotator_event",
    "try_switch_account",
]
