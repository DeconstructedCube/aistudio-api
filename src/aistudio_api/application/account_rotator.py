"""Account rotation and sticky dispatch for multi-account management."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aistudio_api.config import resolve_rotator_state_file, settings
from aistudio_api.infrastructure.utils.common import atomic_write_json

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
    auth_errors: int = 0
    last_used: float = 0.0
    last_rate_limited: float = 0.0
    last_auth_error: float = 0.0
    auth_cooldown: float = 0.0
    rate_limited_date_la: str | None = None
    model_cooldowns: dict[str, float] = field(default_factory=dict)
    model_rate_limited_dates: dict[str, str] = field(default_factory=dict)
    model_requests: dict[str, int] = field(default_factory=dict)
    model_rate_limited: dict[str, int] = field(default_factory=dict)
    model_drip_mode: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "requests": self.requests,
            "success": self.success,
            "rate_limited": self.rate_limited,
            "errors": self.errors,
            "auth_errors": self.auth_errors,
            "last_used": self.last_used,
            "last_rate_limited": self.last_rate_limited,
            "last_auth_error": self.last_auth_error,
            "auth_cooldown": self.auth_cooldown,
            "rate_limited_date_la": self.rate_limited_date_la,
            "model_cooldowns": dict(self.model_cooldowns),
            "model_rate_limited_dates": dict(self.model_rate_limited_dates),
            "model_requests": dict(self.model_requests),
            "model_rate_limited": dict(self.model_rate_limited),
            "model_drip_mode": dict(self.model_drip_mode),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountStats:
        raw_cd = data.get("model_cooldowns")
        model_cooldowns: dict[str, float] = {}
        if isinstance(raw_cd, dict):
            for k, v in raw_cd.items():
                with contextlib.suppress(ValueError, TypeError):
                    model_cooldowns[str(k)] = float(str(v))

        raw_dates = data.get("model_rate_limited_dates")
        model_rate_limited_dates: dict[str, str] = {}
        if isinstance(raw_dates, dict):
            for k, v in raw_dates.items():
                model_rate_limited_dates[str(k)] = str(v)

        raw_reqs = data.get("model_requests")
        model_requests: dict[str, int] = {}
        if isinstance(raw_reqs, dict):
            for k, v in raw_reqs.items():
                with contextlib.suppress(ValueError, TypeError):
                    model_requests[str(k)] = int(str(v))

        raw_limits = data.get("model_rate_limited")
        model_rate_limited: dict[str, int] = {}
        if isinstance(raw_limits, dict):
            for k, v in raw_limits.items():
                with contextlib.suppress(ValueError, TypeError):
                    model_rate_limited[str(k)] = int(str(v))

        raw_drip = data.get("model_drip_mode")
        model_drip_mode: dict[str, bool] = {}
        if isinstance(raw_drip, dict):
            for k, v in raw_drip.items():
                model_drip_mode[str(k)] = bool(v)
        def _as_int(v: object) -> int:
            try:
                return int(str(v)) if v is not None else 0
            except (ValueError, TypeError):
                return 0

        def _as_float(v: object) -> float:
            try:
                return float(str(v)) if v is not None else 0.0
            except (ValueError, TypeError):
                return 0.0

        return cls(
            account_id=str(data.get("account_id") or ""),
            requests=_as_int(data.get("requests")),
            success=_as_int(data.get("success")),
            rate_limited=_as_int(data.get("rate_limited")),
            errors=_as_int(data.get("errors")),
            auth_errors=_as_int(data.get("auth_errors")),
            last_used=_as_float(data.get("last_used")),
            last_rate_limited=_as_float(data.get("last_rate_limited")),
            last_auth_error=_as_float(data.get("last_auth_error")),
            auth_cooldown=_as_float(data.get("auth_cooldown")),
            rate_limited_date_la=str(data["rate_limited_date_la"]) if data.get("rate_limited_date_la") else None,
            model_cooldowns=model_cooldowns,
            model_requests=model_requests,
            model_rate_limited=model_rate_limited,
            model_drip_mode=model_drip_mode,
        )
    def is_available(
        self, model: str | None = None, *, ignore_auth_cooldown: bool = False
    ) -> bool:
        """检查账号在指定模型下是否可用（美西 0 点自动刷新）。"""
        now = time.time()
        if not ignore_auth_cooldown and self.auth_cooldown > now:
            return False

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
            if limit_date and limit_date < current_la:
                self.model_rate_limited_dates.pop(model, None)
                self.model_cooldowns.pop(model, None)
                self.model_rate_limited.pop(model, None)
                self.model_drip_mode.pop(model, None)

            cd = self.model_cooldowns.get(model, 0.0)
            return now >= cd

        return True

    def get_cooldown_remaining(self, model: str | None = None) -> float:
        """获取距离配额刷新的剩余秒数。"""
        if self.is_available(model):
            return 0.0
        now = time.time()
        if self.auth_cooldown > now:
            return max(0.0, self.auth_cooldown - now)
        if model:
            cd = self.model_cooldowns.get(model, 0.0)
            if cd > now:
                return max(0.0, cd - now)
        return get_seconds_until_pacific_midnight()

    def record_success(self, model: str | None = None) -> None:
        now = time.time()
        self.requests += 1
        self.success += 1
        self.last_used = now
        self.auth_errors = 0
        self.auth_cooldown = 0.0
        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1
            self.model_cooldowns.pop(model, None)
            self.model_rate_limited_dates.pop(model, None)
            # 在 drip 模式下消费了 1 次恢复额度：保持 drip 模式（上限 1-2 次，不盲目重置为满额大号），
            # 若非 drip 模式，则清空限流计数
            if not self.model_drip_mode.get(model):
                self.model_rate_limited.pop(model, None)
    def record_rate_limited(self, model: str | None = None) -> None:
        now = time.time()
        self.requests += 1
        self.rate_limited += 1
        self.last_rate_limited = now
        la_date = get_pacific_date_key(now)
        cooldown_seconds = get_seconds_until_pacific_midnight()

        if model:
            # 检查是否已跨过美西午夜，如果是新的一天则重置该模型的限流计数
            prev_date = self.model_rate_limited_dates.get(model)
            if prev_date and prev_date < la_date:
                self.model_rate_limited.pop(model, None)
                self.model_rate_limited_dates.pop(model, None)
                self.model_cooldowns.pop(model, None)
                self.model_drip_mode.pop(model, None)

            is_drip = bool(self.model_drip_mode.get(model))
            limit_count = self.model_rate_limited.get(model, 0) + 1
            self.model_requests[model] = self.model_requests.get(model, 0) + 1
            self.model_rate_limited[model] = limit_count

            # 动态阶梯与滴漏恢复冷却：
            # 1. 如果已处于滴漏模式 (drip_mode)，说明大额度早已耗尽，恢复出来的 1-2 次已用完，
            #    立即进入 300s (5分钟) 滴漏冷却等待下一次令牌桶滴入，绝不频繁 60s 冲击上游浪费时间；
            # 2. 如果处于初始状态：
            #    - 第 1-2 次: 60s (应对短时并发/RPM 抖动，不轻易断定大额度耗尽)
            #    - 第 3 次起: 判定大额度用尽，进入 drip_mode，并冷却 300s (5分钟) 等待额度渗漏恢复；
            #    - 达到第 5 次连续无可用额度时适度延长至 600s (10分钟)，封顶 1800s，绝不死锁一整天！
            if is_drip:
                cooldown_duration = 300.0 if limit_count <= 4 else 600.0
            elif limit_count <= 2:
                cooldown_duration = 60.0
            else:
                self.model_drip_mode[model] = True
                cooldown_duration = 300.0

            effective_cooldown = min(cooldown_duration, max(60.0, cooldown_seconds))
            self.model_cooldowns[model] = now + effective_cooldown
            self.model_rate_limited_dates[model] = la_date
            self.rate_limited_date_la = la_date if model is None else None
    def record_error(self, model: str | None = None) -> None:
        self.requests += 1
        self.errors += 1
        self.last_used = time.time()
        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1

    def record_auth_error(
        self, model: str | None = None, cooldown_seconds: float = 300.0
    ) -> None:
        """记录鉴权/权限错误（403/401），立即冷却该账号以便快速故障转移。"""
        now = time.time()
        self.requests += 1
        self.errors += 1
        self.auth_errors += 1
        self.last_auth_error = now
        self.last_used = now
        self.auth_cooldown = now + cooldown_seconds
        if model:
            self.model_requests[model] = self.model_requests.get(model, 0) + 1

    def clear_cooldown(self, model: str | None = None) -> None:
        """手动清除冷却与 429/403 锁定。"""
        self.auth_cooldown = 0.0
        self.auth_errors = 0
        if model:
            self.model_cooldowns.pop(model, None)
            self.model_rate_limited_dates.pop(model, None)
            self.model_rate_limited.pop(model, None)
            self.model_drip_mode.pop(model, None)
        else:
            self.rate_limited_date_la = None
            self.model_cooldowns.clear()
            self.model_rate_limited_dates.clear()
            self.model_rate_limited.clear()
            self.model_drip_mode.clear()

class AccountRotator:
    """黏性账号调度管理器。

    默认保持当前激活账号，直到特定模型遇到 429 配额耗尽时，
    自动切换到该模型今日仍有额度的下一个健康账号。
    """

    def __init__(self, account_store: AccountStore) -> None:
        self._store = account_store
        self._stats: dict[str, AccountStats] = {}
        self._lock = asyncio.Lock()

        self.load_state()
        for account in self._store.list_accounts():
            if account.id not in self._stats:
                self._stats[account.id] = AccountStats(account_id=account.id)

    def load_state(self) -> None:
        """从持久化文件恢复账号统计与 429 锁定状态。"""
        if not settings.persist_rotator:
            return
        state_file = resolve_rotator_state_file()
        if not state_file.is_file():
            return
        try:
            raw = state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                for acc_id, item in data.items():
                    if isinstance(item, dict):
                        self._stats[str(acc_id)] = AccountStats.from_dict(item)
                logger.info("已从 %s 恢复 %d 个账号的运行与限额状态", state_file, len(self._stats))
        except Exception as e:
            logger.warning("从 %s 读取账号调度状态失败: %s", state_file, e)

    def save_state(self) -> None:
        """将账号调度统计与 429 冷却状态持久化到文件。"""
        if not settings.persist_rotator:
            return
        state_file = resolve_rotator_state_file()
        try:
            payload = {
                acc_id: stats.to_dict()
                for acc_id, stats in self._stats.items()
            }
            atomic_write_json(state_file, payload)
        except Exception as e:
            logger.warning("持久化账号调度状态到 %s 失败: %s", state_file, e)
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
                    datetime.fromtimestamp(stats.last_rate_limited, tz=UTC).isoformat()
                    if stats.last_rate_limited
                    else None
                ),
                "is_available": stats.is_available(),
                "cooldown_remaining": int(stats.get_cooldown_remaining()),
                "model_cooldowns": {
                    m: int(stats.get_cooldown_remaining(m))
                    for m in (
                        set(stats.model_cooldowns.keys())
                        | {
                            k
                            for k, dt in stats.model_rate_limited_dates.items()
                            if dt == current_la
                        }
                    )
                    if not stats.is_available(m)
                    and int(stats.get_cooldown_remaining(m)) > 0
                },
                "model_rate_limited_dates": dict(stats.model_rate_limited_dates),
                "model_requests": dict(stats.model_requests),
                "model_rate_limited": dict(stats.model_rate_limited),
                "model_drip_mode": dict(stats.model_drip_mode),
            }
        return result

    def _get_available_accounts(
        self, model: str | None = None, *, ignore_auth_cooldown: bool = False
    ) -> list[tuple[AccountMeta, AccountStats]]:
        """获取在指定模型下尚未耗尽当日额度且未被鉴权锁定的账号列表。"""
        accounts = self._store.list_accounts()
        available: list[tuple[AccountMeta, AccountStats]] = []
        for account in accounts:
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            if stats.is_available(model, ignore_auth_cooldown=ignore_auth_cooldown):
                available.append((account, stats))
        return available

    async def get_next_account(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
        failed_account_id: str | None = None,
    ) -> AccountMeta | None:
        """获取下一个可用账号（排除失败账号，优先平滑故障转移至健康账号）。"""
        async with self._lock:
            available = self._get_available_accounts(model)
            if not available:
                # 保底：若所有账号都处于 auth_cooldown 中，允许 fallback 尝试恢复
                available = self._get_available_accounts(
                    model, ignore_auth_cooldown=True
                )
                if not available:
                    return None

            # 优先在排除失败账号的候选集中挑选
            candidates = [
                (a, s)
                for a, s in available
                if not (failed_account_id and a.id == failed_account_id)
            ]
            if candidates:
                # 黏性策略：如果当前账号未失败且在候选列表中，继续复用
                if current_account_id and not (
                    failed_account_id and current_account_id == failed_account_id
                ):
                    for a, _ in candidates:
                        if a.id == current_account_id:
                            return a
                # 故障转移：按最少 auth_errors、最少 errors、最久未用挑选
                # 故障转移：优先非 drip_mode 的充沛额度账号，然后按最少 auth_errors、最少 errors、最久未用挑选
                account, _ = min(
                    candidates,
                    key=lambda x: (
                        1 if (model and x[1].model_drip_mode.get(model)) else 0,
                        x[1].auth_errors,
                        x[1].errors,
                        x[1].last_used,
                    ),
                )
                if current_account_id and account.id != current_account_id:
                    logger.info("账号故障转移切换: %s (model=%s)", account.name, model)
                return account
            # 如果没有其他可用账号（如单账号或全部其他账号均 429 耗尽）：
            if failed_account_id:
                for a, _ in available:
                    if a.id == failed_account_id:
                        return a

            return available[0][0]

    async def get_next_account_with_stats(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
        failed_account_id: str | None = None,
    ) -> tuple[AccountMeta, AccountStats] | None:
        """获取下一个可用账号及其统计。"""
        async with self._lock:
            available = self._get_available_accounts(model)
            if not available:
                available = self._get_available_accounts(
                    model, ignore_auth_cooldown=True
                )
                if not available:
                    return None

            candidates = [
                (a, s)
                for a, s in available
                if not (failed_account_id and a.id == failed_account_id)
            ]
            if candidates:
                if current_account_id and not (
                    failed_account_id and current_account_id == failed_account_id
                ):
                    for a, s in candidates:
                        if a.id == current_account_id:
                            return a, s
                return min(
                    candidates,
                    key=lambda x: (
                        1 if (model and x[1].model_drip_mode.get(model)) else 0,
                        x[1].auth_errors,
                        x[1].errors,
                        x[1].last_used,
                    ),
                )
            if failed_account_id:
                for a, s in available:
                    if a.id == failed_account_id:
                        return a, s

            return available[0]

    def record_success(self, account_id: str, model: str | None = None) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_success(model)

        self.save_state()
    def record_rate_limited(
        self,
        account_id: str,
        model: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_rate_limited(model)
        logger.warning("账号 %s 在模型 %s 触发当日限额", account_id, model or "all")

        self.save_state()
    def record_error(self, account_id: str, model: str | None = None) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_error(model)
        self.save_state()

    def record_auth_error(
        self,
        account_id: str,
        model: str | None = None,
        cooldown_seconds: float = 300.0,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_auth_error(
            model, cooldown_seconds=cooldown_seconds
        )
        logger.warning(
            "账号 %s 遇到 403 权限拒绝，暂停使用 %ds 并切换备用账号",
            account_id,
            int(cooldown_seconds),
        )
        self.save_state()
    def clear_cooldown(self, account_id: str, model: str | None = None) -> None:
        """清除指定账号的 429 锁定。"""
        if account_id in self._stats:
            self._stats[account_id].clear_cooldown(model)
            logger.info("已手动清除账号 %s 锁定 (model=%s)", account_id, model)

        self.save_state()
    def clear_all_cooldowns(self) -> None:
        """清除全部账号的所有模型 429 锁定。"""
        for stats in self._stats.values():
            stats.clear_cooldown()
        logger.info("已手动清除全部账号的 429 锁定状态")

        self.save_state()
    def add_account(self, account_id: str) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)

        self.save_state()
    def remove_account(self, account_id: str) -> None:
        self._stats.pop(account_id, None)

        self.save_state()

_rotator: AccountRotator | None = None


def get_rotator() -> AccountRotator | None:
    return _rotator


def init_rotator(account_store: AccountStore) -> AccountRotator:
    global _rotator
    _rotator = AccountRotator(account_store)
    return _rotator
