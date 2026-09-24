"""Unit tests for runtime statistics and account rotator state persistence."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore


def test_model_stats_persistence(tmp_path, monkeypatch):
    stats_file = tmp_path / "data" / "stats.json"
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_STATS", "1")

    state1 = RuntimeState()
    state1.record(
        "gemini-3.7-flash",
        "success",
        {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )
    state1.record("gemini-3.7-flash", "rate_limited")
    state1.record("gemini-3.1-flash-image-preview", "errors")

    assert stats_file.is_file()
    saved = json.loads(stats_file.read_text(encoding="utf-8"))
    assert saved["gemini-3.7-flash"]["requests"] == 2
    assert saved["gemini-3.7-flash"]["success"] == 1
    assert saved["gemini-3.7-flash"]["rate_limited"] == 1
    assert saved["gemini-3.7-flash"]["prompt_tokens"] == 10
    assert saved["gemini-3.7-flash"]["total_tokens"] == 30
    assert saved["gemini-3.1-flash-image-preview"]["errors"] == 1

    # Simulate restart by creating a new RuntimeState instance
    state2 = RuntimeState()
    assert state2.model_stats["gemini-3.7-flash"].requests == 2
    assert state2.model_stats["gemini-3.7-flash"].success == 1
    assert state2.model_stats["gemini-3.7-flash"].rate_limited == 1
    assert state2.model_stats["gemini-3.7-flash"].total_tokens == 30
    assert state2.model_stats["gemini-3.1-flash-image-preview"].errors == 1


def test_account_rotator_state_persistence(tmp_path, monkeypatch):
    rotator_file = tmp_path / "data" / "rotator_state.json"
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_ROTATOR", "1")

    mock_store = MagicMock(spec=AccountStore)
    mock_store.list_accounts.return_value = [
        AccountMeta(
            id="acc_test1",
            name="Account 1",
            email="acc1@gmail.com",
            created_at="2026-09-21 00:00:00",
        ),
        AccountMeta(
            id="acc_test2",
            name="Account 2",
            email="acc2@gmail.com",
            created_at="2026-09-21 00:00:00",
        ),
    ]

    rotator1 = AccountRotator(mock_store)
    rotator1.record_success("acc_test1", "gemini-3.7-flash")
    rotator1.record_rate_limited("acc_test1", "gemini-3.7-flash")
    rotator1.record_rate_limited("acc_test1", "gemini-3.7-flash")
    rotator1.record_rate_limited(
        "acc_test1", "gemini-3.7-flash"
    )  # triggers 3rd 429 -> locks to midnight
    rotator1.record_auth_error("acc_test2", "gemini-3.7-flash", cooldown_seconds=600.0)

    assert rotator_file.is_file()
    saved = json.loads(rotator_file.read_text(encoding="utf-8"))
    assert "acc_test1" in saved
    assert saved["acc_test1"]["requests"] == 4
    assert saved["acc_test1"]["rate_limited"] == 3
    assert "gemini-3.7-flash" in saved["acc_test1"]["model_rate_limited_dates"]
    assert saved["acc_test2"]["auth_errors"] == 1
    assert saved["acc_test2"]["auth_cooldown"] > 0

    # Simulate restart by creating a new AccountRotator instance
    rotator2 = AccountRotator(mock_store)
    all_stats = rotator2.get_all_stats()
    assert all_stats["acc_test1"]["requests"] == 4
    assert all_stats["acc_test1"]["rate_limited"] == 3
    assert not rotator2._stats["acc_test1"].is_available("gemini-3.7-flash")
    assert not rotator2._stats["acc_test2"].is_available("gemini-3.7-flash")
