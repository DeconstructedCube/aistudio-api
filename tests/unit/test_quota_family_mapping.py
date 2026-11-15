"""共享额度分组重复或冲突时，仍须正确保留自身冷却与家族隔离。"""

from __future__ import annotations

import pytest

from aistudio_api.application.account_rotator import AccountStats
from aistudio_api.infrastructure.gateway.model_discovery import ModelDiscoveryService


def _family(name: str, *members: str) -> list[object]:
    return [name, None, None, None, [[f"models/{member}"] for member in members]]


def _install(
    rows: list[list[object]], monkeypatch: pytest.MonkeyPatch
) -> ModelDiscoveryService:
    service = ModelDiscoveryService()
    service._parse_quota_models([rows])
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.model_discovery.model_discovery", service
    )
    return service


def test_repeated_family_rows_preserve_every_members_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install(
        [_family("shared", "model-a"), _family("shared", "model-b")], monkeypatch
    )
    stats = AccountStats(account_id="acc_quota")
    stats.record_rate_limited(model="model-a")

    assert not stats.is_available("model-a")
    assert not stats.is_available("model-b")
    assert stats.get_cooldown_remaining("model-a") > 0
    assert set(service.get_quota_family_siblings("model-a")) == {
        "model-a",
        "model-b",
    }

    stats.clear_cooldown("model-b")
    assert stats.is_available("model-a")
    assert stats.is_available("model-b")


def test_reassigned_member_does_not_link_unrelated_family_cooldowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install(
        [
            _family("first", "model-a", "model-b"),
            _family("second", "model-b", "model-c"),
        ],
        monkeypatch,
    )
    stats = AccountStats(account_id="acc_quota")
    stats.record_rate_limited(model="model-b")

    assert stats.is_available("model-a")
    assert not stats.is_available("model-b")
    assert not stats.is_available("model-c")
    assert service.resolve_quota_family("model-b") == "second"
    assert service.get_quota_family_siblings("model-a") == ("model-a",)

    stats.clear_cooldown("model-a")
    assert not stats.is_available("model-b")
    assert not stats.is_available("model-c")

    stats.record_rate_limited(model="model-a")
    stats.clear_cooldown("model-c")
    assert not stats.is_available("model-a")
    assert stats.is_available("model-b")
    assert stats.is_available("model-c")
