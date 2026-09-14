"""Account rotation for multi-account load balancing."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore

logger = logging.getLogger("aistudio.rotator")


class RotationMode(str, Enum):
    """轮询模式。"""

    STICKY = "sticky"  # 默认逮着一个号薅直到429
    ROUND_ROBIN = "round_robin"  # 顺序轮询
    LEAST_RECENTLY_USED = "lru"  # 最久未用
    LEAST_RATE_LIMITED = "least_rl"  # 最少限流

def get_pacific_date_key(ts: float | None = None) -> str:
    """获取美西太平洋时间（America/Los_Angeles）日期键值 YYYY-MM-DD。"""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Los_Angeles")
        dt = datetime.fromtimestamp(ts or time.time(), tz=tz)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        # 简易保底（UTC-8 / UTC-7）
        dt = datetime.fromtimestamp((ts or time.time()) - 28800, tz=UTC)
        return dt.strftime("%Y-%m-%d")


@dataclass
class AccountStats:
    """单账号的运行统计。"""

    account_id: str
    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    last_used: float = 0.0  # timestamp
    last_rate_limited: float = 0.0  # timestamp
    cooldown_until: float = 0.0  # timestamp, 全局 429 冷却期
    rate_limited_date_la: str | None = None
    model_cooldowns: dict[str, float] = field(default_factory=dict)
    model_rate_limited_dates: dict[str, str] = field(default_factory=dict)
    model_requests: dict[str, int] = field(default_factory=dict)
    model_rate_limited: dict[str, int] = field(default_factory=dict)

    def is_available(self, model: str | None = None) -> bool:
        """检查账号是否可用（指定模型或全局可用）。"""
        now = time.time()
        current_la = get_pacific_date_key()

        # 检查全局冷却与美西日限额
        if self.rate_limited_date_la:
            if self.rate_limited_date_la < current_la:
                self.rate_limited_date_la = None
                self.cooldown_until = 0.0

        if now < self.cooldown_until:
            return False

        if model:
            # 清理美西过期的模型冷却
            limit_date = self.model_rate_limited_dates.get(model)
            if limit_date and limit_date < current_la:
                self.model_rate_limited_dates.pop(model, None)
                self.model_cooldowns.pop(model, None)

            cd = self.model_cooldowns.get(model, 0.0)
            return now >= cd

        return True

    def get_cooldown_remaining(self, model: str | None = None) -> float:
        now = time.time()
        if not self.is_available(model):
            global_cd = max(0.0, self.cooldown_until - now)
            if model:
                model_cd = max(0.0, self.model_cooldowns.get(model, 0.0) - now)
                return max(global_cd, model_cd)
            return global_cd
        return 0.0

    def record_success(self, model: str | None = None) -> None:
        now = time.time()
        self.requests += 1
        self.success += 1
        self.last_used = now
        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1
            self.model_cooldowns.pop(model, None)
            self.model_rate_limited_dates.pop(model, None)

    def record_rate_limited(
        self, model: str | None = None, cooldown_seconds: int = 60
    ) -> None:
        now = time.time()
        self.requests += 1
        self.rate_limited += 1
        self.last_rate_limited = now
        la_date = get_pacific_date_key()

        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1
            self.model_rate_limited[model] = (
                self.model_rate_limited.get(model, 0) + 1
            )
            self.model_cooldowns[model] = now + cooldown_seconds
            self.model_rate_limited_dates[model] = la_date
        else:
            self.rate_limited_date_la = la_date
            self.cooldown_until = now + cooldown_seconds

    def record_error(self, model: str | None = None) -> None:
        self.requests += 1
        self.errors += 1
        self.last_used = time.time()
        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1

class AccountRotator:
    """多账号轮询管理器。

    支持三种模式：
    - round_robin: 顺序轮询，429 时跳过冷却中的账号
    - lru: 最久未用优先，适合均匀分配负载
    - least_rl: 最少限流优先，适合最大化吞吐
    """

    def __init__(
        self,
        account_store: AccountStore,
        mode: RotationMode = RotationMode.ROUND_ROBIN,
        cooldown_seconds: int = 60,
    ) -> None:
        self._store = account_store
        self._mode = mode
        self._cooldown_seconds = cooldown_seconds
        self._stats: dict[str, AccountStats] = {}
        self._current_index: int = 0
        self._lock = asyncio.Lock()

        # 初始化已有账号的统计
        for account in self._store.list_accounts():
            if account.id not in self._stats:
                self._stats[account.id] = AccountStats(account_id=account.id)

    @property
    def mode(self) -> RotationMode:
        return self._mode

    @mode.setter
    def mode(self, value: RotationMode) -> None:
        logger.info("轮询模式切换: %s -> %s", self._mode, value)
        self._mode = value

    @property
    def cooldown_seconds(self) -> int:
        return self._cooldown_seconds

    @cooldown_seconds.setter
    def cooldown_seconds(self, value: int) -> None:
        self._cooldown_seconds = value

    def get_all_stats(self) -> dict[str, dict[str, object]]:
        """获取所有账号的统计信息。"""
        result = {}
        for account in self._store.list_accounts():
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            result[account.id] = {
                "name": account.name,
                "email": account.email,
                "requests": stats.requests,
                "success": stats.success,
                "rate_limited": stats.rate_limited,
                "errors": stats.errors,
                "last_used": datetime.fromtimestamp(stats.last_used, tz=UTC).isoformat()
                if stats.last_used
                else None,
                "last_rate_limited": datetime.fromtimestamp(
                    stats.last_rate_limited, tz=UTC
                ).isoformat()
                if stats.last_rate_limited
                else None,
                "is_available": stats.is_available(),
                "cooldown_remaining": max(0, int(stats.cooldown_until - time.time())),
                "model_cooldowns": {
                    m: max(0, int(cd - time.time()))
                    for m, cd in stats.model_cooldowns.items()
                },
                "model_requests": dict(stats.model_requests),
                "model_rate_limited": dict(stats.model_rate_limited),
            }
        return result

    def _get_available_accounts(
        self, model: str | None = None
    ) -> list[tuple[AccountMeta, AccountStats]]:
        """获取所有可用的账号（不在冷却期）。"""
        accounts = self._store.list_accounts()
        available = []
        for account in accounts:
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            if stats.is_available(model):
                available.append((account, stats))
        return available
    def _pick_round_robin(
        self, available: list[tuple[AccountMeta, AccountStats]]
    ) -> tuple[AccountMeta, AccountStats] | None:
        """Round-robin 选择，基于全量账号索引，避免 available 变化导致跳过或重复。"""
        if not available:
            return None
        available_ids = {a.id for a, _ in available}
        all_accounts = self._store.list_accounts()
        if not all_accounts:
            return None
        total = len(all_accounts)
        for i in range(total):
            idx = (self._current_index + i) % total
            if all_accounts[idx].id in available_ids:
                self._current_index = (idx + 1) % total
                return next(
                    (a, s) for a, s in available if a.id == all_accounts[idx].id
                )
        return available[0]

    def _pick_lru(
        self, available: list[tuple[AccountMeta, AccountStats]]
    ) -> tuple[AccountMeta, AccountStats] | None:
        """最久未用优先。"""
        if not available:
            return None
        return min(
            available,
            key=lambda x: x[1].last_used if x[1].last_used > 0 else float("inf"),
        )

    def _pick_least_rl(
        self, available: list[tuple[AccountMeta, AccountStats]]
    ) -> tuple[AccountMeta, AccountStats] | None:
        """最少限流优先。"""
        if not available:
            return None
        return min(available, key=lambda x: x[1].rate_limited)

    async def get_next_account(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
    ) -> AccountMeta | None:
        """获取下一个可用的账号。"""
        async with self._lock:
            available = self._get_available_accounts(model)

            if not available:
                # 所有账号都在冷却期，找一个冷却时间最短的
                all_accounts = self._store.list_accounts()
                if not all_accounts:
                    return None
                # 选冷却结束最早的
                earliest = min(
                    [
                        (a, self._stats.get(a.id, AccountStats(account_id=a.id)))
                        for a in all_accounts
                    ],
                    key=lambda x: x[1].get_cooldown_remaining(model),
                )
                account, stats = earliest
                wait_time = stats.get_cooldown_remaining(model)
                logger.warning(
                    "所有账号对模型 %s 均在冷却期，等待 %.1fs 使用 %s",
                    model or "all",
                    wait_time,
                    account.name,
                )
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                return account

            # 根据模式选择
            if self._mode == RotationMode.STICKY:
                if current_account_id:
                    for a, _ in available:
                        if a.id == current_account_id:
                            return a
                pick = self._pick_least_rl(available) or available[0]
            elif self._mode == RotationMode.ROUND_ROBIN:
                pick = self._pick_round_robin(available)
            elif self._mode == RotationMode.LEAST_RECENTLY_USED:
                pick = self._pick_lru(available)
            elif self._mode == RotationMode.LEAST_RATE_LIMITED:
                pick = self._pick_least_rl(available)
            else:
                pick = available[0]

            if pick is None:
                return None

            account, stats = pick
            logger.info(
                "轮询选择账号: %s (mode=%s, model=%s)", account.name, self._mode, model
            )
            return account
    async def get_next_account_with_stats(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
    ) -> tuple[AccountMeta, AccountStats] | None:
        """获取下一个可用的账号及其统计。"""
        async with self._lock:
            available = self._get_available_accounts(model)
            if not available:
                all_accounts = self._store.list_accounts()
                if not all_accounts:
                    return None
                earliest = min(
                    [
                        (a, self._stats.get(a.id, AccountStats(account_id=a.id)))
                        for a in all_accounts
                    ],
                    key=lambda x: x[1].get_cooldown_remaining(model),
                )
                account, stats = earliest
                wait_time = stats.get_cooldown_remaining(model)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                return account, stats

            if self._mode == RotationMode.STICKY:
                if current_account_id:
                    for a, s in available:
                        if a.id == current_account_id:
                            return a, s
                pick = self._pick_least_rl(available) or available[0]
            elif self._mode == RotationMode.ROUND_ROBIN:
                pick = self._pick_round_robin(available)
            elif self._mode == RotationMode.LEAST_RECENTLY_USED:
                pick = self._pick_lru(available)
            elif self._mode == RotationMode.LEAST_RATE_LIMITED:
                pick = self._pick_least_rl(available)
            else:
                pick = available[0]
            return pick

    def record_success(self, account_id: str, model: str | None = None) -> None:
        """记录成功请求。"""
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_success(model)

    def record_rate_limited(
        self,
        account_id: str,
        model: str | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        """记录 429 限流。"""
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        cd = (
            cooldown_seconds
            if cooldown_seconds is not None
            else self._cooldown_seconds
        )
        self._stats[account_id].record_rate_limited(model, cd)
        logger.warning(
            "账号 %s (model=%s) 被限流，冷却 %ds",
            account_id,
            model or "all",
            cd,
        )

    def record_error(self, account_id: str, model: str | None = None) -> None:
        """记录错误。"""
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_error(model)
    def add_account(self, account_id: str) -> None:
        """添加新账号时初始化统计。"""
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)

    def remove_account(self, account_id: str) -> None:
        """删除账号时清理统计。"""
        self._stats.pop(account_id, None)


# 全局轮询器实例
_rotator: AccountRotator | None = None


def get_rotator() -> AccountRotator | None:
    """获取全局轮询器。"""
    return _rotator


def init_rotator(account_store: AccountStore, **kwargs) -> AccountRotator:
    """初始化全局轮询器。"""
    global _rotator
    _rotator = AccountRotator(account_store, **kwargs)
    return _rotator
