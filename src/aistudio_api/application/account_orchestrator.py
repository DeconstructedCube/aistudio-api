"""Account failover and concurrency orchestration."""

from __future__ import annotations

import asyncio
import time

from aistudio_api.api.state import runtime_state
from aistudio_api.infrastructure.gateway.session import GatewaySession, SessionContext
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("orchestrator")
MAX_RETRIES = 5


def get_max_retries() -> int:
    """Resolve effective max_retries from config.yaml (or respect test-monkeypatched MAX_RETRIES)."""
    if MAX_RETRIES != 5:
        return max(1, int(MAX_RETRIES))
    try:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_rotator_settings,
        )

        cfg = get_configured_rotator_settings()
        raw = cfg.get("max_retries")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return max(1, int(raw))
    except Exception:
        pass
    return max(1, int(MAX_RETRIES))


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


def _is_session_bound_to_account(account_id: str) -> bool:
    """注册表目标、凭据路径、Cookie 代际、登录索引和权益一致时才能复用会话。"""
    account_service = runtime_state.account_service
    client = runtime_state.client
    if account_service is None or client is None or client.session is None:
        return False
    account = account_service.get_active_account()
    context = client.session.active_context
    if (
        account is None
        or account.id != account_id
        or not account.enabled
        or not isinstance(context, SessionContext)
        or not client.session.is_ready
        or not context.cookies
    ):
        return False
    auth_path = account_service.get_account_auth_path(account_id)
    if auth_path is None or context.auth_file != str(auth_path):
        return False
    from aistudio_api.infrastructure.account.cookie_parser import normalize_benefit_tier

    return bool(
        context.cookie_id == (account.cookie_id or "")
        and context.auth_user == (account.auth_user or "0")
        and normalize_benefit_tier(context.tier) == normalize_benefit_tier(account.tier)
    )


def _clear_gateway_context() -> None:
    """无可用目标时清空内存凭据，禁止旧的禁用会话继续被新请求使用。"""
    client = runtime_state.client
    if client is not None and client.session is not None:
        client.session.set_ready_context(SessionContext())


async def _ensure_bound_account(
    account_id: str, *, force_refresh: bool = False
) -> bool:
    """持有切号锁时完成实际会话交接，并验证激活结果。"""
    global _last_switch_time
    if not force_refresh and _is_session_bound_to_account(account_id):
        return True
    account_service = runtime_state.account_service
    client = runtime_state.client
    if account_service is None or client is None or client.session is None:
        _clear_gateway_context()
        return False
    account = account_service.get_account(account_id)
    if (
        account is None
        or not account.enabled
        or account_service.get_account_auth_path(account_id) is None
    ):
        _clear_gateway_context()
        return False
    try:
        result = await account_service.activate_account(account_id, client.session)
    except Exception:
        _clear_gateway_context()
        raise
    if result is None or not _is_session_bound_to_account(account_id):
        _clear_gateway_context()
        return False
    _last_switch_time = time.time()
    return True


async def try_switch_account(
    model: str | None = None,
    failed_account_id: str | None = None,
    required_tiers: tuple[str, ...] | list[str] | None = None,
    *,
    is_session_expired: bool = False,
    expected_cookie_id: str | None = None,
) -> bool:
    """尝试切换到下一个对目标 model 可用的账号。防止并发级联切号。"""
    global _last_switch_time
    entry_time = time.time()

    if model and not required_tiers:
        from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

        tiers = model_discovery.get_required_tiers(model)
        if tiers:
            required_tiers = tuple(tiers)

    from aistudio_api.infrastructure.account.cookie_parser import normalize_benefit_tier

    allowed_tier_keys = (
        {normalize_benefit_tier(t).lower() for t in required_tiers}
        if required_tiers
        else set()
    )

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
                and (
                    not allowed_tier_keys
                    or normalize_benefit_tier(cur_active.tier).lower()
                    in allowed_tier_keys
                )
                and rotator.is_account_available(cur_active.id, model=model)
                and _is_session_bound_to_account(cur_active.id)
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
            _clear_gateway_context()
            return False

        current_active = account_service.get_active_account()
        current_id = current_active.id if current_active else None

        # 等待切号锁期间可能已重新导入，旧认证结果不得处罚新代际。
        if (
            failed_account_id
            and expected_cookie_id is not None
            and not account_service.is_cookie_generation_current(
                failed_account_id, expected_cookie_id
            )
        ):
            logger.info(
                "忽略等待切号锁期间过期的认证结果: account=%s", failed_account_id
            )
            if (
                current_active is not None
                and current_id
                and (
                    not allowed_tier_keys
                    or normalize_benefit_tier(current_active.tier).lower()
                    in allowed_tier_keys
                )
                and rotator.is_account_available(current_id, model=model)
            ):
                return await _ensure_bound_account(current_id)
            # 忽略旧代际处罚，但仍须选择并绑定一个当前可用账号。
            failed_account_id = None
            is_session_expired = False

        # 当前代际的失效必须先记到失败账号，随后才能复用并发已切换的健康会话。
        if failed_account_id and is_session_expired:
            rotator.record_session_expired(failed_account_id, model=model)

        # 双重检查 1：如果已有并发协程在 entry_time 之后完成切号或在位自愈刷新，且当前账号对该模型可用，则直接复用
        if (
            _last_switch_time >= entry_time
            and current_id
            and (not is_session_expired or current_id != failed_account_id)
            and (
                not allowed_tier_keys
                or (
                    current_active
                    and normalize_benefit_tier(current_active.tier).lower()
                    in allowed_tier_keys
                )
            )
            and rotator.is_account_available(current_id, model=model)
        ):
            logger.info(
                "复用并发已切换账号: account=%s, model=%s",
                current_id,
                model,
            )
            return await _ensure_bound_account(current_id)
        # 双重检查 2：如果已经由并发协程切换到了新账号，且新账号对当前 model 可用，则直接复用
        if (
            failed_account_id
            and current_id
            and current_id != failed_account_id
            and (
                not allowed_tier_keys
                or (
                    current_active
                    and normalize_benefit_tier(current_active.tier).lower()
                    in allowed_tier_keys
                )
            )
            and rotator.is_account_available(current_id, model=model)
        ):
            logger.info(
                "复用并发已切换账号: account=%s, model=%s",
                current_id,
                model,
            )
            return await _ensure_bound_account(current_id)
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
                    "账号 %s 登录态失效且无备用账号可用",
                    failed_account_id,
                )
            else:
                logger.warning(
                    "无可用备用账号: model=%s, failed=%s",
                    model,
                    failed_account_id,
                )
            _clear_gateway_context()
            return False

        if (
            current_id is None
            or next_account.id != current_id
            or not _is_session_bound_to_account(next_account.id)
        ):
            activated = await _ensure_bound_account(next_account.id, force_refresh=True)
            if activated:
                rotator.clear_cooldown(next_account.id, model=model)
                _last_switch_time = time.time()
                logger.info(
                    "切换账号: %s -> %s (tier=%s), model=%s",
                    current_id,
                    next_account.id,
                    next_account.tier,
                    model,
                )
            return activated
        # 登录态失效严禁在原账号原地重试
        if is_session_expired and (
            failed_account_id == current_id or next_account.id == failed_account_id
        ):
            logger.error("账号 %s 登录态已失效，跳过原地重试", failed_account_id)
            _clear_gateway_context()
            return False

        # 单账号模式或所有其他账号均不可用时（非登录态失效，如偶发 403 鉴权波动），如果指定了 failed_account_id，强制刷新当前会话
        if failed_account_id and failed_account_id == current_id:
            logger.info(
                "无其他备用账号，重置当前会话: account=%s",
                current_id,
            )
            activated = await _ensure_bound_account(current_id, force_refresh=True)
            if activated:
                rotator.clear_cooldown(current_id, model=model)
                _last_switch_time = time.time()
            return activated
        if (
            current_id
            and (
                not allowed_tier_keys
                or (
                    current_active
                    and normalize_benefit_tier(current_active.tier).lower()
                    in allowed_tier_keys
                )
            )
            and rotator.is_account_available(current_id, model=model)
        ):
            return await _ensure_bound_account(current_id)
        _clear_gateway_context()
        return False


async def _resolve_active_session_context() -> SessionContext | None:
    """解析并返回当前活跃账号对应的内存 SessionContext 快照。"""
    client = runtime_state.client
    session = getattr(client, "session", None) if client is not None else None
    if not isinstance(session, GatewaySession):
        return None
    account_svc = runtime_state.account_service
    if account_svc is not None:
        active_acc = account_svc.get_active_account()
        if active_acc is not None:
            auth_path = account_svc.get_account_auth_path(active_acc.id)
            if auth_path is not None:
                return await session.transition_to_account(
                    str(auth_path), force_refresh=False
                )
    if session.is_ready:
        return session.active_context
    return None


async def ensure_active_account(
    attempt: int, model: str | None = None
) -> SessionContext | None:
    """每次尝试核对真实会话绑定并返回 SessionContext 快照，权益不足时抢切，无可用账号时立即拒绝。"""
    from fastapi import HTTPException

    account_svc = runtime_state.account_service
    rotator = runtime_state.rotator
    current = account_svc.get_active_account() if account_svc else None
    allowed_tiers: set[str] | None = None

    # 核心拦截：检测目标模型是否为 Paid 模型且当前账号层级不满足要求（含重试期间被并发请求切到 Free 的场景）
    if model:
        from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

        if model_discovery.is_paid_model(model):
            req_tier = model_discovery.get_required_tier(model)
            req_tiers = model_discovery.get_required_tiers(model)
            from aistudio_api.infrastructure.account.cookie_parser import (
                normalize_benefit_tier,
            )

            allowed_tiers = (
                {normalize_benefit_tier(t).lower() for t in req_tiers}
                if req_tiers
                else (
                    {"pro", "ultra", "ultra_20x"}
                    if req_tier == "Pro"
                    else {"ultra", "ultra_20x"}
                )
            )
            cur_tier = normalize_benefit_tier(current.tier if current else None)
            if not current or cur_tier.lower() not in allowed_tiers:
                logger.info(
                    "模型 %s 要求 %s 订阅，当前账号 %s (%s)，切换至对应等级账号",
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
                        "模型 %s 要求 %s 订阅，当前无可用对应等级账号",
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
                return await _resolve_active_session_context()

    if (
        current is not None
        and rotator is not None
        and rotator.is_account_available(current.id, model=model)
    ):
        if _is_session_bound_to_account(current.id):
            return await _resolve_active_session_context()
        async with _get_switch_lock():
            from aistudio_api.infrastructure.account.cookie_parser import (
                normalize_benefit_tier,
            )

            # 等锁期间目标可能变化，只为最新且可用的注册表目标同步凭据。
            current = account_svc.get_active_account() if account_svc else None
            if (
                current is not None
                and (
                    not allowed_tiers
                    or normalize_benefit_tier(current.tier).lower() in allowed_tiers
                )
                and rotator.is_account_available(current.id, model=model)
            ):
                logger.info(
                    "同步注册表与实际会话: account=%s, 尝试=%d",
                    current.id,
                    attempt + 1,
                )
                if await _ensure_bound_account(current.id):
                    return await _resolve_active_session_context()
                # 当前账号凭据损坏时退出锁，继续尝试健康备用账号。

    if not await try_switch_account(
        model=model, failed_account_id=current.id if current else None
    ):
        raise HTTPException(503, detail="No available accounts for this request.")
    return await _resolve_active_session_context()


def record_rotator_event(
    event: str,
    model: str | None = None,
    usage: dict[str, object] | None = None,
    account_id: str | None = None,
) -> None:
    """记录指定账号事件；未指定时沿用活跃账号，显式未知目标不能回退。"""
    rotator = runtime_state.rotator
    if not rotator:
        return
    account_service = runtime_state.account_service
    if account_id is not None:
        if not account_id or (
            account_service is not None
            and account_service.get_account(account_id) is None
        ):
            return
        target_account_id = account_id
    else:
        account = account_service.get_active_account() if account_service else None
        if account is None:
            return
        target_account_id = account.id
    if event == "success":
        rotator.record_success(target_account_id, model=model, usage=usage)
    elif event == "rate_limited":
        rotator.record_rate_limited(target_account_id, model=model)
    elif event == "auth_error":
        rotator.record_auth_error(target_account_id, model=model)
    elif event == "error":
        rotator.record_error(target_account_id, model=model)


__all__ = [
    "MAX_RETRIES",
    "ensure_active_account",
    "get_max_retries",
    "record_rotator_event",
    "try_switch_account",
]
