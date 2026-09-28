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
from aistudio_api.application.account_rotator import AccountRotator, AccountStats
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
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

    # acc_2 is available for target model
    mock_rotator.is_account_available = MagicMock(return_value=True)

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
    mock_rotator.is_account_available = MagicMock(return_value=False)

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service

    try:
        runtime_state.rotator = mock_rotator
        runtime_state.account_service = mock_service

        # attempt > 0 does nothing
        await ensure_active_account(attempt=1, model="gemini-2.5-flash")

        # attempt == 0 with unavailable model calls try_switch_account
        mock_rotator.is_account_available.assert_not_called()
        await ensure_active_account(attempt=0, model="gemini-2.5-flash")
        mock_rotator.is_account_available.assert_called_with(
            "acc_1", model="gemini-2.5-flash"
        )
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
    mock_client.clear_templates = MagicMock()

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
        mock_client.clear_templates.assert_called_once()
        mock_service.activate_account.assert_called_once_with(
            "acc_1",
            mock_client._session,
        )
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client


@pytest.mark.asyncio
async def test_try_switch_account_session_expired_strictly_forbids_in_place_retry():
    """登录态失效时严禁在原账号原地重试，无备用账号时直接返回 False。"""
    from unittest.mock import AsyncMock

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock(spec=AIStudioClient)
    mock_client._session = MagicMock()

    active_acc = AccountMeta(
        id="acc_1",
        name="Single Account",
        email="single@gmail.com",
        created_at="2026-01-01",
    )
    mock_service.get_active_account = MagicMock(return_value=active_acc)
    mock_service.activate_account = AsyncMock()
    # 当 is_session_expired=True 且无其他备用账号时，rotator 返回 None
    mock_rotator.get_next_account = AsyncMock(return_value=None)

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service
    orig_client = runtime_state.client

    try:
        runtime_state.rotator = mock_rotator
        runtime_state.account_service = mock_service
        runtime_state.client = mock_client

        result = await try_switch_account(
            model="gemini-3.8-flash",
            failed_account_id="acc_1",
            is_auth_error=True,
            is_session_expired=True,
        )
        # 严禁原地重试，必须返回 False
        assert result is False
        mock_rotator.record_session_expired.assert_called_once_with(
            "acc_1", model="gemini-3.8-flash"
        )
        mock_service.activate_account.assert_not_called()
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client


@pytest.mark.asyncio
async def test_try_switch_account_session_expired_switches_to_healthy_account():
    """同 Cookie 或备用账号中某一个失效时，仅该账号失效并平滑切换到同 Cookie 的另一个正常子账号。"""
    from unittest.mock import AsyncMock

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock(spec=AIStudioClient)
    mock_client._session = MagicMock()

    acc1 = AccountMeta(
        id="acc_1", name="Cookie A u/0", email="user@gmail.com", created_at="2026-01-01"
    )
    acc2 = AccountMeta(
        id="acc_2", name="Cookie A u/1", email="user@gmail.com", created_at="2026-01-01"
    )

    mock_service.get_active_account = MagicMock(return_value=acc1)
    mock_service.activate_account = AsyncMock(return_value=acc2)
    # 成功挑中同 Cookie 的备用子账号 acc_2
    mock_rotator.get_next_account = AsyncMock(return_value=acc2)

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service
    orig_client = runtime_state.client

    try:
        runtime_state.rotator = mock_rotator
        runtime_state.account_service = mock_service
        runtime_state.client = mock_client

        result = await try_switch_account(
            model="gemini-3.8-flash",
            failed_account_id="acc_1",
            is_auth_error=True,
            is_session_expired=True,
        )
        assert result is True
        # 仅禁用失效的 acc_1
        mock_rotator.record_session_expired.assert_called_once_with(
            "acc_1", model="gemini-3.8-flash"
        )
        # 成功激活 acc_2
        mock_service.activate_account.assert_called_once_with(
            "acc_2",
            mock_client._session,
        )
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client


def test_account_stats_session_expired_permanent_isolation():
    """账号标记为 session_expired 后处于永久禁用状态，美西跨天不自动重置，ignore_auth_cooldown 也不可用。"""
    stats = AccountStats(account_id="acc_test")
    assert stats.is_available("gemini-3.8-flash")

    stats.record_session_expired(model="gemini-3.8-flash")
    assert stats.session_expired is True
    assert not stats.is_available("gemini-3.8-flash")
    # 即使 ignore_auth_cooldown 也绝对不可用
    assert not stats.is_available("gemini-3.8-flash", ignore_auth_cooldown=True)

    # 模拟跨天
    stats._check_date_reset("gemini-3.8-flash", "2099-01-01")
    assert not stats.is_available("gemini-3.8-flash")
    assert stats.session_expired is True

    # 仅手动清除冷却/重新导入时恢复
    stats.clear_cooldown()
    assert stats.session_expired is False
    assert stats.is_available("gemini-3.8-flash")


def test_account_stats_short_cooldown_sync():
    """Verify short 60s cooldown is tracked accurately for monitoring."""
    stats = AccountStats(account_id="acc_test")
    stats.record_rate_limited(model="gemini-2.5-flash")

    assert not stats.is_available("gemini-2.5-flash")
    rem = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert 0 < rem <= 60.0

    stats.record_rate_limited(model="gemini-2.5-flash")
    assert not stats.is_available("gemini-2.5-flash")
    rem2 = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert 0 < rem2 <= 60.0

    stats.record_rate_limited(model="gemini-2.5-flash")
    assert not stats.is_available("gemini-2.5-flash")
    rem3 = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert rem3 > 60.0


def test_safe_account_dir_path_traversal_rejection(tmp_path):
    """Verify _safe_account_dir rejects path traversal and malicious account IDs."""
    from aistudio_api.infrastructure.account.account_store import _safe_account_dir

    base = tmp_path / "accounts"
    base.mkdir()

    # Valid account IDs
    valid_dir = _safe_account_dir(base, "user_123")
    assert valid_dir == (base / "user_123").resolve()

    valid_email = _safe_account_dir(base, "test.user@gmail.com")
    assert valid_email == (base / "test.user@gmail.com").resolve()

    # Invalid / traversal IDs
    for bad_id in [
        "../etc/passwd",
        "..",
        "user/subdir",
        "user\\subdir",
        "foo/../bar",
        "",
    ]:
        with pytest.raises(ValueError):
            _safe_account_dir(base, bad_id)


def test_cleanup_ghost_accounts():
    """测试幽灵账号清理：已被删除但残留在 rotator_state 中的失效/429 账号被自动检测并彻底剔除。"""
    store = MagicMock(spec=AccountStore)
    real_acc = AccountMeta(
        id="real_acc", name="Real", email="real@gmail.com", created_at="2026-01-01"
    )
    store.list_accounts.return_value = [real_acc]

    rotator = AccountRotator(account_store=store)
    # 模拟 rotator_state.json 中残留了已经被删除的幽灵账号 ghost_acc_1 与 ghost_acc_2 (带着 429 状态)
    rotator._stats["ghost_acc_1"] = AccountStats(
        account_id="ghost_acc_1", rate_limited=5
    )
    rotator._stats["ghost_acc_2"] = AccountStats(
        account_id="ghost_acc_2", session_expired=True
    )

    assert "ghost_acc_1" in rotator._stats
    assert "ghost_acc_2" in rotator._stats
    assert "real_acc" in rotator._stats

    cleaned = rotator.cleanup_ghost_accounts()
    assert set(cleaned) == {"ghost_acc_1", "ghost_acc_2"}
    assert "ghost_acc_1" not in rotator._stats
    assert "ghost_acc_2" not in rotator._stats
    assert "real_acc" in rotator._stats

    # 测试单账号删除同步
    rotator.remove_account("real_acc")
    assert "real_acc" not in rotator._stats
