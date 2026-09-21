"""Shared API runtime state."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from aistudio_api.config import resolve_stats_file, settings
from aistudio_api.infrastructure.utils.common import atomic_write_json

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

logger = logging.getLogger("aistudio.state")
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


@dataclass
class RuntimeState:
    client: AIStudioClient | None = None
    browser_port: int = 9222
    snapshot_cache: SnapshotCache | None = None
    account_service: AccountService | None = None
    rotator: AccountRotator | None = None
    model_stats: dict[str, ModelStatsItem] = field(
        default_factory=lambda: defaultdict(ModelStatsItem)
    )

    def __post_init__(self) -> None:
        self.load_stats()

    def load_stats(self) -> None:
        """从持久化文件加载历史调用统计。"""
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        if not stats_path.is_file():
            return
        try:
            raw = stats_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                for model, item in data.items():
                    if isinstance(item, dict):
                        self.model_stats[model] = ModelStatsItem(
                            requests=int(item.get("requests", 0)),
                            success=int(item.get("success", 0)),
                            rate_limited=int(item.get("rate_limited", 0)),
                            errors=int(item.get("errors", 0)),
                            prompt_tokens=int(item.get("prompt_tokens", 0)),
                            completion_tokens=int(item.get("completion_tokens", 0)),
                            total_tokens=int(item.get("total_tokens", 0)),
                            last_used=str(item["last_used"]) if item.get("last_used") else None,
                        )
        except Exception as e:
            logger.warning("从 %s 读取模型统计失败: %s", stats_path, e)

    def save_stats(self) -> None:
        """将模型调用与 Token 统计持久化到文件。"""
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        try:
            payload = {
                model: asdict(item)
                for model, item in self.model_stats.items()
            }
            atomic_write_json(stats_path, payload)
        except Exception as e:
            logger.warning("持久化模型统计到 %s 失败: %s", stats_path, e)
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
        self.save_stats()


runtime_state = RuntimeState()
