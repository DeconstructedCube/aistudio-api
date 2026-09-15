"""Shared API runtime state."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
    from aistudio_api.infrastructure.gateway.client import AIStudioClient


@dataclass
class ModelStatsItem:
    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    last_used: str | None = None


class RuntimeState:
    client: AIStudioClient | None = None
    browser_port: int = 9222
    snapshot_cache: SnapshotCache | None = None
    account_service: AccountService | None = None
    rotator: AccountRotator | None = None
    model_stats: dict[str, ModelStatsItem] = field(
        default_factory=lambda: defaultdict(ModelStatsItem)
    )

    def record(
        self,
        model: str,
        result: str,
        usage: dict[str, object] | None = None,
    ) -> None:
        stats = self.model_stats[model]
        stats.requests += 1
        tz = timezone(timedelta(hours=8))
        stats.last_used = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        if result == "success":
            stats.success += 1
        elif result == "rate_limited":
            stats.rate_limited += 1
        elif result == "errors":
            stats.errors += 1

        if usage:
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)
            tt = usage.get("total_tokens", 0)
            stats.prompt_tokens += pt if isinstance(pt, int) else 0
            stats.completion_tokens += ct if isinstance(ct, int) else 0
            stats.total_tokens += tt if isinstance(tt, int) else 0


runtime_state = RuntimeState()
