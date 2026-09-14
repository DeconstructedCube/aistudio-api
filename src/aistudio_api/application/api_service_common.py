"""Shared helpers for API service handlers."""

from __future__ import annotations

import asyncio
import logging
from fastapi import HTTPException

from aistudio_api.api.response_models import (
    HealthResponse,
    ModelStatsResponse,
    StatsResponse,
    StatsTotalsResponse,
)
from aistudio_api.api.state import runtime_state

logger = logging.getLogger("aistudio.server")
MAX_RETRIES = 3

_switch_lock = asyncio.Lock()


def parse_cooldown_from_error(exc: Exception) -> int:
    """根据 429 报错内容识别是分钟限制 (RPM) 还是每日配额 (RPD)。"""
    msg = str(exc).lower()
    if any(k in msg for k in ("perday", "daily", "day", "quota")):
        return 86400
    return 60


async def try_switch_account(
    model: str | None = None,
    failed_account_id: str | None = None,
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

        # 双重检查：如果已经由并发协程切换到了新账号，且新账号对当前 model 可用，则直接复用
        if failed_account_id and current_id and current_id != failed_account_id:
            stats = rotator._stats.get(current_id)
            if stats and stats.is_available(model):
                logger.info(
                    "检测到已有并发协程将账号切换至 %s 且对 model=%s 可用，直接复用",
                    current_id,
                    model,
                )
                return True

        next_account = await rotator.get_next_account(
            model=model, current_account_id=current_id
        )
        if next_account is None:
            return False

        if current_id and next_account.id == current_id:
            stats = rotator._stats.get(current_id)
            if stats and stats.is_available(model):
                return True
            return False

        result = await account_service.activate_account(
            next_account.id,
            client._session,
            runtime_state.snapshot_cache,
            None,  # skip lock — caller already holds it
            keep_snapshot_cache=False,
        )
        return result is not None
def require_busy_lock():
    busy_lock = runtime_state.busy_lock
    if busy_lock is None:
        raise HTTPException(
            503, detail={"message": "Server not ready", "type": "service_unavailable"}
        )
    if busy_lock.locked():
        raise HTTPException(
            503,
            detail={
                "message": "Server is busy with maximum concurrent requests",
                "type": "service_unavailable",
            },
            headers={"Retry-After": "2"},
        )
    return busy_lock


async def ensure_active_account(attempt: int, model: str | None = None) -> None:
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
    cooldown_seconds: int | None = None,
) -> None:
    rotator = runtime_state.rotator
    account_service = runtime_state.account_service
    account = account_service.get_active_account() if account_service else None
    if not rotator or account is None:
        return
    if event == "success":
        rotator.record_success(account.id, model=model)
    elif event == "rate_limited":
        rotator.record_rate_limited(
            account.id, model=model, cooldown_seconds=cooldown_seconds
        )
    elif event == "error":
        rotator.record_error(account.id, model=model)

def health_response() -> HealthResponse:
    busy_lock = runtime_state.busy_lock
    return HealthResponse(status="ok", busy=busy_lock.locked() if busy_lock else False)


def stats_response() -> StatsResponse:
    stats = dict(runtime_state.model_stats)
    totals = StatsTotalsResponse(
        requests=sum(s.requests for s in stats.values()),
        success=sum(s.success for s in stats.values()),
        rate_limited=sum(s.rate_limited for s in stats.values()),
        errors=sum(s.errors for s in stats.values()),
        prompt_tokens=sum(s.prompt_tokens for s in stats.values()),
        completion_tokens=sum(s.completion_tokens for s in stats.values()),
        total_tokens=sum(s.total_tokens for s in stats.values()),
    )
    models = {
        name: ModelStatsResponse(
            requests=s.requests,
            success=s.success,
            rate_limited=s.rate_limited,
            errors=s.errors,
            prompt_tokens=s.prompt_tokens,
            completion_tokens=s.completion_tokens,
            total_tokens=s.total_tokens,
            last_used=s.last_used,
        )
        for name, s in stats.items()
    }
    return StatsResponse(models=models, totals=totals)
