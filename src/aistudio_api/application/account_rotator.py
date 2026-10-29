"""Account rotation and sticky dispatch for multi-account management."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
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
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("rotator")


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


def normalize_model_key(model: str | None) -> str | None:
    """统一模型标识（去除 models/ 前缀并转小写），确保单模型统计准确隔离。"""
    if not model:
        return None
    return model.removeprefix("models/").lower()


@dataclass(slots=True)
class AccountModelDailyStats:
    """单账号在特定模型当天的统计数据。"""

    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    last_used: float = 0.0
    cooldown_until: float = 0.0
    drip_mode: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountModelDailyStats:
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
            requests=_as_int(data.get("requests")),
            success=_as_int(data.get("success")),
            rate_limited=_as_int(data.get("rate_limited")),
            errors=_as_int(data.get("errors")),
            prompt_tokens=_as_int(data.get("prompt_tokens")),
            completion_tokens=_as_int(data.get("completion_tokens")),
            reasoning_tokens=_as_int(data.get("reasoning_tokens")),
            cached_tokens=_as_int(data.get("cached_tokens")),
            total_tokens=_as_int(data.get("total_tokens")),
            last_used=_as_float(data.get("last_used")),
            cooldown_until=_as_float(data.get("cooldown_until")),
            drip_mode=bool(data.get("drip_mode")),
        )


@dataclass
class AccountStats:
    """账号多维透视与运行调度统计（按 Account x Model x Date 原生划分）。"""

    account_id: str
    total_requests: int = 0
    total_success: int = 0
    total_rate_limited: int = 0
    total_errors: int = 0
    auth_errors: int = 0
    last_used: float = 0.0
    last_rate_limited: float = 0.0
    last_auth_error: float = 0.0
    auth_cooldown: float = 0.0
    session_expired: bool = False
    last_session_expired: float = 0.0
    daily_date_la: str = ""
    daily_models: dict[str, AccountModelDailyStats] = field(default_factory=dict)

    # 快捷属性，保持调用方代码简洁
    @property
    def requests(self) -> int:
        return self.total_requests

    @requests.setter
    def requests(self, val: int) -> None:
        self.total_requests = val

    @property
    def success(self) -> int:
        return self.total_success

    @success.setter
    def success(self, val: int) -> None:
        self.total_success = val

    @property
    def rate_limited(self) -> int:
        return self.total_rate_limited

    @rate_limited.setter
    def rate_limited(self, val: int) -> None:
        self.total_rate_limited = val

    @property
    def errors(self) -> int:
        return self.total_errors

    @errors.setter
    def errors(self, val: int) -> None:
        self.total_errors = val

    def _check_date_reset(self, current_la: str) -> None:
        """若跨入新的一天（美西 00:00 PST），自动清零今日模型统计。"""
        if not self.daily_date_la:
            self.daily_date_la = current_la
            return
        if self.daily_date_la < current_la:
            self.daily_models.clear()
            self.daily_date_la = current_la

    def get_model_daily(self, model: str | None) -> AccountModelDailyStats:
        """获取或创建指定模型在当天的统计对象。"""
        current_la = get_pacific_date_key()
        self._check_date_reset(current_la)
        norm_m = normalize_model_key(model) or "general"
        if norm_m not in self.daily_models:
            self.daily_models[norm_m] = AccountModelDailyStats()
        return self.daily_models[norm_m]

    def is_available(
        self, model: str | None = None, *, ignore_auth_cooldown: bool = False
    ) -> bool:
        """检查账号在指定模型下是否可用（软限额超标不禁用，仅上游真 429 冷却与登录态失效不可用）。"""
        now = time.time()
        if self.session_expired:
            return False
        if not ignore_auth_cooldown and self.auth_cooldown > now:
            return False
        current_la = get_pacific_date_key(now)
        self._check_date_reset(current_la)

        if model:
            norm_m = normalize_model_key(model)
            if norm_m and norm_m in self.daily_models:
                m_stats = self.daily_models[norm_m]
                if m_stats.cooldown_until > now:
                    return False

        return True

    def get_cooldown_remaining(self, model: str | None = None) -> float:
        """获取距离配额刷新的剩余秒数。"""
        if self.is_available(model):
            return 0.0
        now = time.time()
        if self.auth_cooldown > now:
            return max(0.0, self.auth_cooldown - now)
        if model:
            norm_m = normalize_model_key(model)
            if norm_m and norm_m in self.daily_models:
                cd = self.daily_models[norm_m].cooldown_until
                if cd > now:
                    return max(0.0, cd - now)
        return get_seconds_until_pacific_midnight()

    def record_success(
        self, model: str | None = None, usage: dict[str, object] | None = None
    ) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_success += 1
        self.last_used = now
        self.auth_errors = 0
        self.auth_cooldown = 0.0

        m_stats = self.get_model_daily(model)
        m_stats.requests += 1
        m_stats.success += 1
        m_stats.last_used = now
        m_stats.cooldown_until = 0.0
        if not m_stats.drip_mode:
            m_stats.rate_limited = 0

        if usage:
            pt = int(str(usage.get("prompt_tokens") or 0))
            ct = int(str(usage.get("completion_tokens") or 0))
            tt = int(str(usage.get("total_tokens") or 0))
            details = usage.get("completion_tokens_details")
            rt = 0
            if isinstance(details, dict):
                rt = int(str(details.get("reasoning_tokens") or 0))
            cached = int(str(usage.get("cached_tokens") or 0))
            m_stats.prompt_tokens += pt
            m_stats.completion_tokens += ct
            m_stats.reasoning_tokens += rt
            m_stats.cached_tokens += cached
            m_stats.total_tokens += tt if tt else (pt + ct)

    def record_rate_limited(self, model: str | None = None) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_rate_limited += 1
        self.last_rate_limited = now
        cooldown_seconds = get_seconds_until_pacific_midnight()

        m_stats = self.get_model_daily(model)
        m_stats.requests += 1
        m_stats.rate_limited += 1
        m_stats.last_used = now

        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_rotator_settings,
        )

        rot_cfg = get_configured_rotator_settings()
        cooldowns = rot_cfg["cooldowns"]
        if not isinstance(cooldowns, dict):
            raise RuntimeError("rotator 配置缺少 cooldowns 配置块")
        cd_short = float(str(cooldowns["rate_limited_short"]))
        cd_drip = float(str(cooldowns["rate_limited_drip"]))
        cd_drip_ext = float(str(cooldowns["rate_limited_drip_extended"]))

        is_drip = m_stats.drip_mode
        limit_count = m_stats.rate_limited
        if is_drip:
            cooldown_duration = cd_drip if limit_count <= 4 else cd_drip_ext
        elif limit_count <= 2:
            cooldown_duration = cd_short
        else:
            m_stats.drip_mode = True
            cooldown_duration = cd_drip

        effective_cooldown = min(cooldown_duration, max(60.0, cooldown_seconds))
        m_stats.cooldown_until = now + effective_cooldown

    def record_error(self, model: str | None = None) -> None:
        self.total_requests += 1
        self.total_errors += 1
        self.last_used = time.time()
        m_stats = self.get_model_daily(model)
        m_stats.requests += 1
        m_stats.errors += 1
        m_stats.last_used = time.time()

    def record_auth_error(
        self, model: str | None = None, cooldown_seconds: float | None = None
    ) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_errors += 1
        self.auth_errors += 1
        self.last_auth_error = now
        self.last_used = now
        if cooldown_seconds is None:
            from aistudio_api.infrastructure.gateway.model_defaults import (
                get_configured_rotator_settings,
            )

            rot_cfg = get_configured_rotator_settings()
            cooldowns = rot_cfg["cooldowns"]
            if not isinstance(cooldowns, dict):
                raise RuntimeError("rotator 配置缺少 cooldowns 配置块")
            cooldown_seconds = float(str(cooldowns["auth_error"]))
        self.auth_cooldown = now + cooldown_seconds
        m_stats = self.get_model_daily(model)
        m_stats.requests += 1
        m_stats.errors += 1

    def record_session_expired(self, model: str | None = None) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_errors += 1
        self.auth_errors += 1
        self.session_expired = True
        self.last_session_expired = now
        self.last_auth_error = now
        self.last_used = now
        self.auth_cooldown = now + 86400.0
        m_stats = self.get_model_daily(model)
        m_stats.requests += 1
        m_stats.errors += 1

    def clear_cooldown(self, model: str | None = None) -> None:
        """清除冷却与 429/403/失效锁定。"""
        self.auth_cooldown = 0.0
        self.auth_errors = 0
        self.session_expired = False
        self.last_session_expired = 0.0
        if model:
            norm_m = normalize_model_key(model)
            if norm_m and norm_m in self.daily_models:
                st = self.daily_models[norm_m]
                st.cooldown_until = 0.0
                st.rate_limited = 0
                st.drip_mode = False
        else:
            for st in self.daily_models.values():
                st.cooldown_until = 0.0
                st.rate_limited = 0
                st.drip_mode = False

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "total_requests": self.total_requests,
            "total_success": self.total_success,
            "total_rate_limited": self.total_rate_limited,
            "total_errors": self.total_errors,
            "auth_errors": self.auth_errors,
            "last_used": self.last_used,
            "last_rate_limited": self.last_rate_limited,
            "last_auth_error": self.last_auth_error,
            "auth_cooldown": self.auth_cooldown,
            "session_expired": self.session_expired,
            "last_session_expired": self.last_session_expired,
            "daily_date_la": self.daily_date_la,
            "daily_models": {
                m: m_stats.to_dict() for m, m_stats in self.daily_models.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountStats:
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

        raw_daily = data.get("daily_models")
        daily_models: dict[str, AccountModelDailyStats] = {}
        if isinstance(raw_daily, dict):
            for m, m_data in raw_daily.items():
                if isinstance(m_data, dict):
                    daily_models[str(m)] = AccountModelDailyStats.from_dict(m_data)

        # 兼容旧版本保存的扁平字段
        tot_req = _as_int(data.get("total_requests") or data.get("requests"))
        tot_succ = _as_int(data.get("total_success") or data.get("success"))
        tot_limit = _as_int(data.get("total_rate_limited") or data.get("rate_limited"))
        tot_err = _as_int(data.get("total_errors") or data.get("errors"))

        old_model_reqs = data.get("model_requests")
        old_model_cds = data.get("model_cooldowns")
        old_model_rates = data.get("model_rate_limited")
        old_model_drips = data.get("model_drip_mode")
        if not daily_models and (
            isinstance(old_model_reqs, dict)
            or isinstance(old_model_cds, dict)
            or isinstance(old_model_rates, dict)
            or isinstance(old_model_drips, dict)
        ):
            all_keys: set[str] = set()
            for source in (
                old_model_reqs,
                old_model_cds,
                old_model_rates,
                old_model_drips,
            ):
                if isinstance(source, dict):
                    all_keys.update(str(k) for k in source)
            for m_k in all_keys:
                daily_models[str(m_k)] = AccountModelDailyStats(
                    requests=_as_int(old_model_reqs.get(m_k))
                    if isinstance(old_model_reqs, dict)
                    else 0,
                    rate_limited=_as_int(old_model_rates.get(m_k))
                    if isinstance(old_model_rates, dict)
                    else 0,
                    cooldown_until=_as_float(old_model_cds.get(m_k))
                    if isinstance(old_model_cds, dict)
                    else 0.0,
                    drip_mode=bool(old_model_drips.get(m_k))
                    if isinstance(old_model_drips, dict)
                    else False,
                )
        return cls(
            account_id=str(data.get("account_id") or ""),
            total_requests=tot_req,
            total_success=tot_succ,
            total_rate_limited=tot_limit,
            total_errors=tot_err,
            auth_errors=_as_int(data.get("auth_errors")),
            last_used=_as_float(data.get("last_used")),
            last_rate_limited=_as_float(data.get("last_rate_limited")),
            last_auth_error=_as_float(data.get("last_auth_error")),
            auth_cooldown=_as_float(data.get("auth_cooldown")),
            session_expired=bool(data.get("session_expired")),
            last_session_expired=_as_float(data.get("last_session_expired")),
            daily_date_la=str(data.get("daily_date_la") or get_pacific_date_key()),
            daily_models=daily_models,
        )


class AccountRotator:
    """账号调度管理器。

    核心流程：
    1. 候选过滤（使能开关、登录态、分组、订阅要求、429 冷却）；
    2. 基于配额消耗比与订阅保护的打分择优；
    3. 业务分组与方案预设。
    """

    def __init__(self, account_store: AccountStore) -> None:
        self._store = account_store
        self._stats: dict[str, AccountStats] = {}
        self._lock = asyncio.Lock()
        self._last_saved_time: float = 0.0
        self._save_task: asyncio.Task[None] | None = None
        self.load_state()
        self.cleanup_ghost_accounts()
        for account in self._store.list_accounts():
            if account.id not in self._stats:
                self._stats[account.id] = AccountStats(account_id=account.id)

    def load_state(self) -> None:
        """从持久化文件恢复账号统计状态。"""
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
                logger.info(
                    "已从 %s 恢复 %d 个账号的统计状态",
                    state_file,
                    len(self._stats),
                )
        except Exception as e:
            logger.warning("从 %s 读取账号调度状态失败: %s", state_file, e)

    def save_state(self) -> None:
        """将账号调度与统计状态落盘。"""
        if not settings.persist_rotator:
            return
        state_file = resolve_rotator_state_file()
        try:
            payload = {acc_id: stats.to_dict() for acc_id, stats in self._stats.items()}
            atomic_write_json(state_file, payload)
            self._last_saved_time = time.time()
        except Exception as e:
            logger.warning("持久化账号调度状态到 %s 失败: %s", state_file, e)

    def _schedule_debounced_save(self, delay: float = 2.0) -> None:
        if not settings.persist_rotator:
            return
        now = time.time()
        if now - self._last_saved_time >= 5.0:
            self.save_state()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save_state()
            return

        if self._save_task is not None and not self._save_task.done():
            return

        async def _delayed():
            await asyncio.sleep(delay)
            self.save_state()

        self._save_task = loop.create_task(_delayed())

    def get_account_stats(self, account_id: str) -> AccountStats | None:
        """获取指定账号的统计对象。"""
        stats = self._stats.get(account_id)
        if stats is None and any(
            a.id == account_id for a in self._store.list_accounts()
        ):
            stats = AccountStats(account_id=account_id)
            self._stats[account_id] = stats
        return stats

    def is_account_available(self, account_id: str, model: str | None = None) -> bool:
        """检查指定账号在特定模型下是否可用（遵循使能开关、登录态与真 429 冷却）。"""
        account = self._store.get_account(account_id)
        if not account or not account.enabled:
            return False
        stats = self.get_account_stats(account_id)
        return bool(stats and stats.is_available(model))

    def get_all_stats(self) -> dict[str, dict[str, object]]:
        """获取所有账号的运行透视与配额状态。"""
        from aistudio_api.infrastructure.gateway.model_defaults import (
            resolve_tier_quota,
        )

        result: dict[str, dict[str, object]] = {}
        current_la = get_pacific_date_key()

        for account in self._store.list_accounts():
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            stats._check_date_reset(current_la)
            tier_str = getattr(account, "tier", "Free") or "Free"

            today_models_data: dict[str, dict[str, object]] = {}
            model_cooldowns_dict: dict[str, int] = {}
            today_total_requests = 0
            today_total_tokens = 0

            for m_key, m_stats in stats.daily_models.items():
                today_total_requests += m_stats.requests
                today_total_tokens += m_stats.total_tokens
                quota = resolve_tier_quota(tier_str, m_key)
                rem_cd = (
                    max(0, int(m_stats.cooldown_until - time.time()))
                    if m_stats.cooldown_until > time.time()
                    else 0
                )
                if rem_cd > 0:
                    model_cooldowns_dict[m_key] = rem_cd

                today_models_data[m_key] = {
                    "requests": m_stats.requests,
                    "success": m_stats.success,
                    "rate_limited": m_stats.rate_limited,
                    "errors": m_stats.errors,
                    "prompt_tokens": m_stats.prompt_tokens,
                    "completion_tokens": m_stats.completion_tokens,
                    "reasoning_tokens": m_stats.reasoning_tokens,
                    "cached_tokens": m_stats.cached_tokens,
                    "total_tokens": m_stats.total_tokens,
                    "configured_quota": quota,
                    "quota_used_percent": round(
                        (m_stats.requests / max(1, quota)) * 100, 1
                    ),
                    "cooldown_remaining": rem_cd,
                    "drip_mode": m_stats.drip_mode,
                    "is_available": rem_cd == 0,
                }

            result[account.id] = {
                "name": account.name,
                "email": account.email,
                "auth_user": account.auth_user,
                "tier": tier_str,
                "enabled": account.enabled,
                "group": account.group,
                "notes": account.notes,
                "requests": stats.total_requests,
                "success": stats.total_success,
                "rate_limited": stats.total_rate_limited,
                "errors": stats.total_errors,
                "today_total_requests": today_total_requests,
                "today_total_tokens": today_total_tokens,
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
                "is_available": account.enabled and stats.is_available(),
                "session_expired": stats.session_expired,
                "auth_cooldown": max(0, int(stats.auth_cooldown - time.time()))
                if stats.auth_cooldown > time.time()
                else 0,
                "auth_errors": stats.auth_errors,
                "cooldown_remaining": int(stats.get_cooldown_remaining()),
                "model_cooldowns": model_cooldowns_dict,
                "today_models": today_models_data,
                # 保持前端现有组件向下读取容错
                "model_requests": {
                    m: s.requests for m, s in stats.daily_models.items()
                },
                "model_rate_limited": {
                    m: s.rate_limited for m, s in stats.daily_models.items()
                },
                "model_drip_mode": {
                    m: s.drip_mode for m, s in stats.daily_models.items()
                },
            }
        return result

    def _get_eligible_candidates(
        self,
        model: str | None = None,
        required_tiers: tuple[str, ...] | list[str] | None = None,
        active_groups: tuple[str, ...] | list[str] | None = None,
        *,
        ignore_auth_cooldown: bool = False,
    ) -> list[tuple[AccountMeta, AccountStats]]:
        """过滤可用账号候选（使能开关、登录态、分组、订阅准入、429 冷却）。"""
        accounts = self._store.list_accounts()
        candidates: list[tuple[AccountMeta, AccountStats]] = []

        for account in accounts:
            # 1. 主开关门禁
            if not account.enabled:
                continue
            # 2. 业务分组过滤
            if active_groups and account.group not in active_groups:
                continue
            # 3. 目标模型显式要求订阅过滤 (如 Pro/Ultra 抢切)
            if required_tiers and account.tier not in required_tiers:
                continue

            stats = self._stats.setdefault(
                account.id, AccountStats(account_id=account.id)
            )
            # 4. 登录态与真 429/403 冷却检查
            if not stats.is_available(model, ignore_auth_cooldown=ignore_auth_cooldown):
                continue

            candidates.append((account, stats))

        # 5. 上游模型能力要求过滤 (如果未传 required_tiers，按模型自身 accessModes 过滤)
        if model and candidates and not required_tiers:
            try:
                from aistudio_api.infrastructure.gateway.model_discovery import (
                    model_discovery,
                )

                tier_matched = [
                    (acc, st)
                    for acc, st in candidates
                    if model_discovery.is_model_allowed_for_tier(
                        model, getattr(acc, "tier", "Free")
                    )
                ]
                if tier_matched:
                    return tier_matched
            except Exception:
                pass

        return candidates

    def _score_candidate(
        self,
        account: AccountMeta,
        stats: AccountStats,
        model: str | None,
        current_account_id: str | None,
    ) -> float:
        """阶段二：多维软权重评分引擎（配额消耗比、高低配节约、黏性奖励、空闲衰减）。"""
        from aistudio_api.infrastructure.gateway.model_defaults import (
            resolve_tier_quota,
        )

        quota = resolve_tier_quota(account.tier, model)
        m_stats = stats.get_model_daily(model)
        req_today = m_stats.requests

        # 1. 配额消耗比 (0.0 ~ 1.0+，超额不禁用，仅增加成本分)
        exhaustion_rate = req_today / max(1, quota)

        # 2. 调度配置与加权读取 (严格由 config.yaml 驱动，禁止硬编码与 fallback)
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_rotator_settings,
        )

        rot_cfg = get_configured_rotator_settings()
        raw_weights = rot_cfg["tier_weights"]
        if not isinstance(raw_weights, dict):
            raise RuntimeError("rotator 配置缺少 tier_weights 配置块")
        tier_clean = (account.tier or "Free").strip()
        matched_weight = None
        for k, v in raw_weights.items():
            if str(k).lower() == tier_clean.lower():
                matched_weight = float(str(v))
                break
        if matched_weight is None:
            matched_weight = float(str(raw_weights.get("Free", 0.0)))
        tier_weight = matched_weight

        strategy = str(rot_cfg.get("strategy") or "sticky").strip().lower()
        bonus_val = float(str(rot_cfg["stickiness_bonus"]))
        stick_threshold = float(str(rot_cfg["stickiness_threshold"]))
        drip_val = float(str(rot_cfg["drip_penalty"]))

        # 3. 黏性奖励：若启用 sticky 策略且当前账号为活跃账号且未超设定配额阈值，给予减分奖励
        stickiness_bonus = (
            -bonus_val
            if (
                strategy == "sticky"
                and account.id == current_account_id
                and exhaustion_rate < stick_threshold
            )
            else 0.0
        )
        drip_penalty = drip_val if m_stats.drip_mode else 0.0

        # 5. 错误与空闲时间
        now = time.time()
        idle_minutes = max(0.0, (now - stats.last_used) / 60.0)

        # 综合成本分：得分越低越优先选派
        return (
            (exhaustion_rate * 1000.0)
            + (tier_weight * 150.0)
            + (req_today * 5.0)
            + (stats.auth_errors * 200.0)
            + (stats.total_errors * 20.0)
            + drip_penalty
            + stickiness_bonus
            - min(100.0, idle_minutes * 0.5)
        )

    async def get_next_account(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
        failed_account_id: str | None = None,
        required_tiers: tuple[str, ...] | list[str] | None = None,
        active_groups: tuple[str, ...] | list[str] | None = None,
        *,
        is_session_expired: bool = False,
    ) -> AccountMeta | None:
        """通过硬性过滤与软配额打分挑选最佳就绪账号。"""
        async with self._lock:
            candidates = self._get_eligible_candidates(
                model=model,
                required_tiers=required_tiers,
                active_groups=active_groups,
            )
            if not candidates:
                # 保底：若所有账号都处于 auth_cooldown 中，允许 fallback 尝试恢复
                candidates = self._get_eligible_candidates(
                    model=model,
                    required_tiers=required_tiers,
                    active_groups=active_groups,
                    ignore_auth_cooldown=True,
                )
                if not candidates:
                    return None

            # 过滤失败账号
            valid_candidates = [
                (a, s)
                for a, s in candidates
                if not (failed_account_id and a.id == failed_account_id)
            ]
            if not valid_candidates:
                if is_session_expired:
                    return None
                # 单账号在位自愈场景
                if failed_account_id:
                    for a, _ in candidates:
                        if a.id == failed_account_id:
                            return a
                return candidates[0][0]

            # 多维软打分选优
            best_account, _ = min(
                valid_candidates,
                key=lambda item: self._score_candidate(
                    item[0], item[1], model, current_account_id
                ),
            )
            if current_account_id and best_account.id != current_account_id:
                logger.info(
                    "账号智能切换: %s -> %s (model=%s, tier=%s)",
                    current_account_id,
                    best_account.id,
                    model,
                    best_account.tier,
                )
            return best_account

    def record_success(
        self,
        account_id: str,
        model: str | None = None,
        usage: dict[str, object] | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_success(model, usage=usage)
        self._schedule_debounced_save()

    def record_rate_limited(
        self,
        account_id: str,
        model: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_rate_limited(model)
        logger.warning("账号 %s 在模型 %s 触发真实上游 429", account_id, model or "all")
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
        cooldown_seconds: float | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_auth_error(
            model, cooldown_seconds=cooldown_seconds
        )
        logger.warning(
            "账号 %s 遇到 403 权限拒绝，暂停使用并切换备用账号",
            account_id,
        )
        self.save_state()

    def record_session_expired(
        self,
        account_id: str,
        model: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_session_expired(model)
        logger.error("账号 %s 登录态失效，已被永久禁用直至重新导入凭据", account_id)
        self.save_state()

    def clear_cooldown(self, account_id: str, model: str | None = None) -> None:
        """清除指定账号的 429 锁定。"""
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
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

    def cleanup_ghost_accounts(self) -> list[str]:
        """清理已从 AccountStore 中删除但仍残留在 rotator_state 中的幽灵账号。"""
        existing_ids = {a.id for a in self._store.list_accounts()}
        ghost_ids = [
            acc_id for acc_id in list(self._stats.keys()) if acc_id not in existing_ids
        ]
        for gid in ghost_ids:
            self._stats.pop(gid, None)
        if ghost_ids:
            logger.info("已清理 %d 个幽灵账号残留状态: %s", len(ghost_ids), ghost_ids)
            self.save_state()
        return ghost_ids


_rotator: AccountRotator | None = None


def init_rotator(account_store: AccountStore) -> AccountRotator:
    global _rotator
    _rotator = AccountRotator(account_store)
    return _rotator
