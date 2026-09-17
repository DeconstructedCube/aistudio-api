"""Account rotation and sticky dispatch for multi-account management."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )
logger = logging.getLogger("aistudio.rotator")


def get_pacific_date_key(ts: float | None = None) -> str:
    """获取美西太平洋时间（America/Los_Angeles）日期键值 YYYY-MM-DD。"""
    try:
        tz = ZoneInfo("America/Los_Angeles")
        dt = datetime.fromtimestamp(ts or time.time(), tz=tz)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        dt = datetime.fromtimestamp((ts or time.time()) - 28800, tz=UTC)
        return dt.strftime("%Y-%m-%d")


def get_seconds_until_pacific_midnight() -> float:
    """获取距离下一次美西太平洋时间午夜 00:00 的秒数。"""
    try:
        tz = ZoneInfo("America/Los_Angeles")
        now_dt = datetime.now(tz)
        tomorrow_dt = (now_dt + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(0.0, (tomorrow_dt - now_dt).total_seconds())
    except Exception:
        # 保底计算
        now = time.time()
        pacific_offset = -28800  # UTC-8
        now_pacific = now + pacific_offset
        seconds_in_day = 86400
        remaining = seconds_in_day - (int(now_pacific) % seconds_in_day)
        return float(remaining)


@dataclass
class AccountStats:
    """单账号的运行与配额统计。"""

    account_id: str
    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    last_used: float = 0.0
    last_rate_limited: float = 0.0
    rate_limited_date_la: str | None = None
    model_cooldowns: dict[str, float] = field(default_factory=dict)
    model_rate_limited_dates: dict[str, str] = field(default_factory=dict)
    model_requests: dict[str, int] = field(default_factory=dict)
    model_rate_limited: dict[str, int] = field(default_factory=dict)

    def is_available(self, model: str | None = None) -> bool:
        """检查账号在指定模型下是否可用（美西 0 点自动刷新）。"""
        now = time.time()
        current_la = get_pacific_date_key(now)

        # 检查全局 429 标记是否已跨过美西午夜
        if self.rate_limited_date_la:
            if self.rate_limited_date_la < current_la:
                self.rate_limited_date_la = None
            else:
                return False

        if model:
            # 检查指定模型 429 标记是否已跨过美西午夜
            limit_date = self.model_rate_limited_dates.get(model)
            if limit_date:
                if limit_date < current_la:
                    self.model_rate_limited_dates.pop(model, None)
                    self.model_cooldowns.pop(model, None)
                else:
                    return False

            cd = self.model_cooldowns.get(model, 0.0)
            return now >= cd

        return True

    def get_cooldown_remaining(self, model: str | None = None) -> float:
        """获取距离配额刷新的剩余秒数。"""
        if self.is_available(model):
            return 0.0
        return get_seconds_until_pacific_midnight()

    def record_success(self, model: str | None = None) -> None:
        now = time.time()
        self.requests += 1
        self.success += 1
        self.last_used = now
        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1
            self.model_cooldowns.pop(model, None)
            self.model_rate_limited_dates.pop(model, None)

    def record_rate_limited(self, model: str | None = None) -> None:
        now = time.time()
        self.requests += 1
        self.rate_limited += 1
        self.last_rate_limited = now
        la_date = get_pacific_date_key(now)
        cooldown_seconds = get_seconds_until_pacific_midnight()

        if model:
            limit_count = self.model_rate_limited.get(model, 0) + 1
            self.model_requests[model] = self.model_requests.get(model, 0) + 1
            self.model_rate_limited[model] = limit_count
            # 前两次 429 设置 60s 短暂冷却（应对并发/RPM 抖动），连续第 3 次以上才视为当日配额耗尽锁定至美西午夜
            if limit_count <= 2:
                self.model_cooldowns[model] = now + 60.0
            else:
                self.model_cooldowns[model] = now + cooldown_seconds
                self.model_rate_limited_dates[model] = la_date
        else:
            self.rate_limited_date_la = la_date

    def record_error(self, model: str | None = None) -> None:
        self.requests += 1
        self.errors += 1
        self.last_used = time.time()
        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1

    def clear_cooldown(self, model: str | None = None) -> None:
        """手动清除冷却与 429 锁定。"""
        if model:
            self.model_cooldowns.pop(model, None)
            self.model_rate_limited_dates.pop(model, None)
        else:
            self.rate_limited_date_la = None
            self.model_cooldowns.clear()
            self.model_rate_limited_dates.clear()


class AccountRotator:
    """黏性账号调度管理器。

    默认保持当前激活账号，直到特定模型遇到 429 配额耗尽时，
    自动切换到该模型今日仍有额度的下一个健康账号。
    """

    def __init__(self, account_store: AccountStore) -> None:
        self._store = account_store
        self._stats: dict[str, AccountStats] = {}
        self._lock = asyncio.Lock()

        for account in self._store.list_accounts():
            if account.id not in self._stats:
                self._stats[account.id] = AccountStats(account_id=account.id)

    def get_all_stats(self) -> dict[str, dict[str, object]]:
        """获取所有账号的运行与配额状态。"""
        result: dict[str, dict[str, object]] = {}
        current_la = get_pacific_date_key()
        for account in self._store.list_accounts():
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            result[account.id] = {
                "name": account.name,
                "email": account.email,
                "requests": stats.requests,
                "success": stats.success,
                "rate_limited": stats.rate_limited,
                "errors": stats.errors,
                "last_used": (
                    datetime.fromtimestamp(stats.last_used, tz=UTC).isoformat()
                    if stats.last_used
                    else None
                ),
                "last_rate_limited": (
                    datetime.fromtimestamp(
                        stats.last_rate_limited, tz=UTC
                    ).isoformat()
                    if stats.last_rate_limited
                    else None
                ),
                "is_available": stats.is_available(),
                "cooldown_remaining": int(stats.get_cooldown_remaining()),
                "model_cooldowns": {
                    m: int(stats.get_cooldown_remaining(m))
                    for m in stats.model_rate_limited_dates
                    if stats.model_rate_limited_dates.get(m) == current_la
                },
                "model_rate_limited_dates": dict(stats.model_rate_limited_dates),
                "model_requests": dict(stats.model_requests),
                "model_rate_limited": dict(stats.model_rate_limited),
            }
        return result

    def _get_available_accounts(
        self, model: str | None = None
    ) -> list[tuple[AccountMeta, AccountStats]]:
        """获取在指定模型下尚未耗尽当日额度的账号列表。"""
        accounts = self._store.list_accounts()
        available: list[tuple[AccountMeta, AccountStats]] = []
        for account in accounts:
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            if stats.is_available(model):
                available.append((account, stats))
        return available

    async def get_next_account(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
    ) -> AccountMeta | None:
        """获取下一个可用账号（优先保持当前账号，若限流则平滑顺延）。"""
        async with self._lock:
            available = self._get_available_accounts(model)
            if not available:
                return None

            # 黏性策略：如果当前账号对该模型依然可用，继续使用
            if current_account_id:
                for a, _ in available:
                    if a.id == current_account_id:
                        return a

            # 故障转移：按最少错误/最久未用挑选下一个可用账号
            pick = min(
                available,
                key=lambda x: (x[1].errors, x[1].last_used),
            )
            account, _ = pick
            logger.info("黏性调度切换账号: %s (model=%s)", account.name, model)
            return account

    async def get_next_account_with_stats(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
    ) -> tuple[AccountMeta, AccountStats] | None:
        """获取下一个可用账号及其统计。"""
        async with self._lock:
            available = self._get_available_accounts(model)
            if not available:
                return None

            if current_account_id:
                for a, s in available:
                    if a.id == current_account_id:
                        return a, s

            return min(
                available,
                key=lambda x: (x[1].errors, x[1].last_used),
            )

    def record_success(self, account_id: str, model: str | None = None) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_success(model)

    def record_rate_limited(
        self,
        account_id: str,
        model: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_rate_limited(model)
        logger.warning("账号 %s 在模型 %s 触发当日限额", account_id, model or "all")

    def record_error(self, account_id: str, model: str | None = None) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_error(model)

    def clear_cooldown(
        self, account_id: str, model: str | None = None
    ) -> None:
        """清除指定账号的 429 锁定。"""
        if account_id in self._stats:
            self._stats[account_id].clear_cooldown(model)
            logger.info("已手动清除账号 %s 锁定 (model=%s)", account_id, model)

    def clear_all_cooldowns(self) -> None:
        """清除全部账号的所有模型 429 锁定。"""
        for stats in self._stats.values():
            stats.clear_cooldown()
        logger.info("已手动清除全部账号的 429 锁定状态")

    def add_account(self, account_id: str) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)

    def remove_account(self, account_id: str) -> None:
        self._stats.pop(account_id, None)


_rotator: AccountRotator | None = None


def get_rotator() -> AccountRotator | None:
    return _rotator


def init_rotator(account_store: AccountStore) -> AccountRotator:
    global _rotator
    _rotator = AccountRotator(account_store)
    return _rotator
