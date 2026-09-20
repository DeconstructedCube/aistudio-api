"""Unit tests for account_orchestrator module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    ensure_active_account,
    record_rotator_event,
    try_switch_account,
)
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.client import AIStudioClient


@pytest.mark.asyncio
async def test_try_switch_account_returns_false_when_uninitialized():
    # Save original state
    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service
    orig_client = runtime_state.client

    try:
        runtime_state.rotator = None
        assert await try_switch_account() is False
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client


@pytest.mark.asyncio
async def test_try_switch_account_double_check_reuse():
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock(spec=AIStudioClient)
    mock_client._session = MagicMock()

    # Active account is now "acc_2"
    active_acc = AccountMeta(
        id="acc_2",
        name="Account 2",
        email="acc2@gmail.com",
        created_at="2026-01-01",
    )
    mock_service.get_active_account = MagicMock(return_value=active_acc)

    # acc_2 is available
    mock_stats = MagicMock()
    mock_stats.is_available = MagicMock(return_value=True)
    mock_rotator._stats = {"acc_2": mock_stats}

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service
    orig_client = runtime_state.client

    try:
        runtime_state.rotator = mock_rotator
        runtime_state.account_service = mock_service
        runtime_state.client = mock_client

        # Call with failed_account_id = "acc_1", current is already "acc_2" and available
        reused = await try_switch_account(
            model="gemini-2.5-flash", failed_account_id="acc_1"
        )
        assert reused is True
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client


@pytest.mark.asyncio
async def test_ensure_active_account_early_switch():
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)

    active_acc = AccountMeta(
        id="acc_1",
        name="Account 1",
        email="acc1@gmail.com",
        created_at="2026-01-01",
    )
    mock_service.get_active_account = MagicMock(return_value=active_acc)

    # acc_1 is NOT available for this model
    mock_stats = MagicMock()
    mock_stats.is_available = MagicMock(return_value=False)
    mock_rotator._stats = {"acc_1": mock_stats}

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service

    try:
        runtime_state.rotator = mock_rotator
        runtime_state.account_service = mock_service

        # attempt > 0 does nothing
        await ensure_active_account(attempt=1, model="gemini-2.5-flash")

        # attempt == 0 with unavailable model calls try_switch_account
        # mock try_switch_account by checking that rotator stats was checked
        mock_stats.is_available.assert_not_called()
        await ensure_active_account(attempt=0, model="gemini-2.5-flash")
        mock_stats.is_available.assert_called_with("gemini-2.5-flash")
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service


def test_record_rotator_event():
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    active_acc = AccountMeta(
        id="acc_1",
        name="Account 1",
        email="acc1@gmail.com",
        created_at="2026-01-01",
    )
    mock_service.get_active_account = MagicMock(return_value=active_acc)

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service

    try:
        runtime_state.rotator = mock_rotator
        runtime_state.account_service = mock_service

        record_rotator_event("success", model="gemini-2.5-flash")
        mock_rotator.record_success.assert_called_with(
            "acc_1", model="gemini-2.5-flash"
        )

        record_rotator_event("rate_limited", model="gemini-2.5-flash")
        mock_rotator.record_rate_limited.assert_called_with(
            "acc_1", model="gemini-2.5-flash"
        )

        record_rotator_event("auth_error", model="gemini-2.5-flash")
        mock_rotator.record_auth_error.assert_called_with(
            "acc_1", model="gemini-2.5-flash"
        )

        record_rotator_event("error", model="gemini-2.5-flash")
        mock_rotator.record_error.assert_called_with("acc_1", model="gemini-2.5-flash")
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service


@pytest.mark.asyncio
async def test_try_switch_account_single_account_recovery():
    """单账号发生 403 鉴权错误时，自动触发当前会话与 BotGuard 强制重建。"""
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock(spec=AIStudioClient)
    mock_client._session = MagicMock()
    mock_client.clear_snapshot_cache = MagicMock()

    active_acc = AccountMeta(
        id="acc_1",
        name="Single Account",
        email="single@gmail.com",
        created_at="2026-01-01",
    )
    mock_service.get_active_account = MagicMock(return_value=active_acc)
    mock_service.activate_account = MagicMock(return_value=active_acc)
    # 模拟 rotator 只有唯一账号 acc_1
    from unittest.mock import AsyncMock

    mock_service.activate_account = AsyncMock(return_value=active_acc)
    mock_rotator.get_next_account = AsyncMock(return_value=active_acc)

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service
    orig_client = runtime_state.client

    try:
        runtime_state.rotator = mock_rotator
        runtime_state.account_service = mock_service
        runtime_state.client = mock_client

        recovered = await try_switch_account(
            model="gemini-3.8-flash",
            failed_account_id="acc_1",
            is_auth_error=True,
        )
        assert recovered is True
        mock_client.clear_snapshot_cache.assert_called_once()
        mock_service.activate_account.assert_called_once_with(
            "acc_1",
            mock_client._session,
            runtime_state.snapshot_cache,
            None,
            keep_snapshot_cache=False,
        )
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client
