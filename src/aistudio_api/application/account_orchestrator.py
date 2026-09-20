"""Account failover and concurrency orchestration."""

from __future__ import annotations

import asyncio
import logging

from aistudio_api.api.state import runtime_state

logger = logging.getLogger("aistudio.server")
MAX_RETRIES = 5

_switch_lock = asyncio.Lock()


async def try_switch_account(
    model: str | None = None,
    failed_account_id: str | None = None,
    *,
    is_auth_error: bool = False,
) -> bool:
    """尝试切换到下一个对目标 model 可用的账号。防止并发级联切号。"""
    async with _switch_lock:
        rotator = runtime_state.rotator
        account_service = runtime_state.account_service
        client = runtime_state.client

        if (
            rotator is None
            or account_service is None
            or client is None
            or client._session is None
        ):
            return False

        current_active = account_service.get_active_account()
        current_id = current_active.id if current_active else None

        # 遇到鉴权错误时，立即记录 auth_error 并触发该账号的短时隔离
        if failed_account_id and is_auth_error:
            rotator.record_auth_error(failed_account_id, model=model)

        # 双重检查：如果已经由并发协程切换到了新账号，且新账号对当前 model 可用，则直接复用
        if failed_account_id and current_id and current_id != failed_account_id:
            stats = rotator._stats.get(current_id)
            if stats and stats.is_available(model):
                logger.info(
                    "已有并发请求完成切号，直接复用当前健康账号: %s (model=%s)",
                    current_id,
                    model,
                )
                return True

        next_account = await rotator.get_next_account(
            model=model,
            current_account_id=current_id,
            failed_account_id=failed_account_id,
        )
        if next_account is None:
            return False

        if current_id is None or next_account.id != current_id:
            result = await account_service.activate_account(
                next_account.id,
                client._session,
                runtime_state.snapshot_cache,
                None,
                keep_snapshot_cache=False,
            )
            return result is not None

        # 单账号模式或所有其他账号均不可用时，如果指定了 failed_account_id，强制刷新当前会话与 BotGuard
        if failed_account_id and failed_account_id == current_id:
            logger.info(
                "无其他可用备用账号，重新刷新当前账号会话与 BotGuard: %s",
                current_id,
            )
            client.clear_snapshot_cache()
            result = await account_service.activate_account(
                current_id,
                client._session,
                runtime_state.snapshot_cache,
                None,
                keep_snapshot_cache=False,
            )
            return result is not None

        stats = rotator._stats.get(current_id)
        return bool(stats and stats.is_available(model))


async def ensure_active_account(attempt: int, model: str | None = None) -> None:
    """确保在初次尝试时存在活跃账号，且该账号对目标模型可用。"""
    if attempt != 0:
        return
    account_svc = runtime_state.account_service
    rotator = runtime_state.rotator
    current = account_svc.get_active_account() if account_svc else None
    if not current:
        await try_switch_account(model=model)
    elif rotator and model:
        stats = rotator._stats.get(current.id)
        if stats and not stats.is_available(model):
            # 当前账号对该模型已限流，提前切号
            await try_switch_account(model=model, failed_account_id=current.id)


def record_rotator_event(
    event: str,
    model: str | None = None,
) -> None:
    """记录调度器事件（成功、限流、错误、鉴权异常）。"""
    rotator = runtime_state.rotator
    account_service = runtime_state.account_service
    account = account_service.get_active_account() if account_service else None
    if not rotator or account is None:
        return
    if event == "success":
        rotator.record_success(account.id, model=model)
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
