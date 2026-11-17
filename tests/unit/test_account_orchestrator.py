"""Unit tests for account_orchestrator module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

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
from aistudio_api.infrastructure.gateway.session import GatewaySession


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
async def test_try_switch_account_double_check_reuse(tmp_path, monkeypatch):
    """已完成真实绑定时可以复用，不能把注册表变化当作实际切号。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    await service.activate_account(second.id, session)
    original_context = session.active_context
    activation = AsyncMock(wraps=service.activate_account)
    monkeypatch.setattr(service, "activate_account", activation)

    assert await try_switch_account(
        model="gemini-2.5-flash", failed_account_id=first.id
    )
    activation.assert_not_awaited()
    assert session.active_context is original_context


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [0, 1])
async def test_ensure_active_account_early_switch(tmp_path, monkeypatch, attempt):
    """每次尝试都检查模型可用性，已限流的当前账号必须交接到备用账号。"""
    service, session, rotator, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    rotator.record_rate_limited(first.id, model="gemini-2.5-flash")

    await ensure_active_account(attempt=attempt, model="gemini-2.5-flash")

    current = service.get_active_account()
    assert current is not None and current.id == second.id
    assert session.active_context is not None
    assert session.active_context.auth_file == str(
        service.get_account_auth_path(second.id)
    )


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
            "acc_1", model="gemini-2.5-flash", usage=None
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
async def test_try_switch_account_single_account_recovery(tmp_path, monkeypatch):
    """单账号发生暂时错误时刷新真实会话，不仅检查激活函数的返回值。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    service.toggle_account_enabled(second.id, False)
    original_context = session.active_context
    activation = AsyncMock(wraps=service.activate_account)
    monkeypatch.setattr(service, "activate_account", activation)

    assert await try_switch_account(
        model="gemini-3.8-flash", failed_account_id=first.id
    )
    activation.assert_awaited_once_with(first.id, session)
    assert session.active_context is not None
    assert session.active_context is not original_context
    assert session.active_context.cookies["SID"] == "first-cookie"


@pytest.mark.asyncio
async def test_try_switch_account_session_expired_strictly_forbids_in_place_retry(
    tmp_path, monkeypatch
):
    """登录态失效时不得原地刷新，无备用账号时清空会话并返回失败。"""
    service, session, rotator, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    service.toggle_account_enabled(second.id, False)
    activation = AsyncMock(wraps=service.activate_account)
    monkeypatch.setattr(service, "activate_account", activation)

    assert not await try_switch_account(
        model="gemini-3.8-flash",
        failed_account_id=first.id,
        is_session_expired=True,
    )
    stats = rotator.get_account_stats(first.id)
    assert stats is not None and stats.session_expired
    activation.assert_not_awaited()
    assert not session.is_ready


@pytest.mark.asyncio
async def test_try_switch_account_session_expired_switches_to_healthy_account(
    tmp_path, monkeypatch
):
    """仅隔离实际失效账号，并把真实会话交接到健康备用账号。"""
    service, session, rotator, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    activation = AsyncMock(wraps=service.activate_account)
    monkeypatch.setattr(service, "activate_account", activation)

    assert await try_switch_account(
        model="gemini-3.8-flash",
        failed_account_id=first.id,
        is_session_expired=True,
    )
    activation.assert_awaited_once_with(second.id, session)
    first_stats = rotator.get_account_stats(first.id)
    second_stats = rotator.get_account_stats(second.id)
    assert first_stats is not None and first_stats.session_expired
    assert second_stats is not None and not second_stats.session_expired
    assert session.active_context is not None
    assert session.active_context.cookie_id == second.cookie_id


def test_account_stats_session_expired_permanent_isolation():
    """账号标记为 session_expired 后处于永久禁用状态，美西跨天不自动重置。"""
    stats = AccountStats(account_id="acc_test")
    assert stats.is_available("gemini-3.8-flash")

    stats.record_session_expired(model="gemini-3.8-flash")
    assert stats.session_expired is True
    assert not stats.is_available("gemini-3.8-flash")
    # 模拟跨天
    stats._check_date_reset("2099-01-01")
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
    # 使用公共 API 模拟持久化恢复或历史遗留的失效/429 幽灵账号
    rotator.add_account("ghost_acc_1")
    rotator.record_rate_limited("ghost_acc_1")
    rotator.add_account("ghost_acc_2")
    rotator.record_session_expired("ghost_acc_2")

    cleaned = rotator.cleanup_ghost_accounts()
    assert set(cleaned) == {"ghost_acc_1", "ghost_acc_2"}
    assert "ghost_acc_1" not in rotator.get_all_stats()
    assert "ghost_acc_2" not in rotator.get_all_stats()
    assert "real_acc" in rotator.get_all_stats()

    # 测试单账号删除同步
    rotator.remove_account("real_acc")
    assert "real_acc" not in rotator._stats


@pytest.mark.asyncio
async def test_newly_imported_account_manual_activation_stays_sticky_on_first_request(
    tmp_path,
):
    """Regression for Issue #7:
    When a new account is imported while the server is running and manually activated,
    the first generateContent request must NOT switch back to the old account due to missing rotator stats.
    """
    from unittest.mock import AsyncMock

    store = AccountStore(accounts_dir=tmp_path / "accounts_issue7")
    store.save_account(
        name="Old Account",
        email="old@gmail.com",
        storage_state={"cookies": [{"name": "SID", "value": "old"}]},
    )
    # Initialize rotator with only old_acc existing at startup
    rotator = AccountRotator(account_store=store)
    service = AccountService(store)

    # Import new_acc at runtime after rotator was already initialized
    new_acc = service.save_account_from_cookies(
        name="New Account",
        email="new@gmail.com",
        storage_state={"cookies": [{"name": "SID", "value": "new"}]},
    )
    service.set_active_account(new_acc.id)

    # Even before explicit rotator.add_account(), rotator must recognize new_acc from store as available
    assert rotator.is_account_available(new_acc.id, model="gemini-3.8-flash") is True
    assert rotator.get_account_stats(new_acc.id) is not None

    mock_client = MagicMock(spec=AIStudioClient)
    mock_client.session = GatewaySession()
    await service.activate_account(new_acc.id, mock_client.session)
    service.activate_account = AsyncMock(wraps=service.activate_account)

    orig_rotator = runtime_state.rotator
    orig_service = runtime_state.account_service
    orig_client = runtime_state.client
    try:
        runtime_state.rotator = rotator
        runtime_state.account_service = service
        runtime_state.client = mock_client

        await ensure_active_account(attempt=0, model="models/gemini-3.8-flash")

        # Active account must remain new_acc and never fail over to old_acc
        active = service.get_active_account()
        assert active is not None
        assert active.id == new_acc.id
        service.activate_account.assert_not_awaited()
    finally:
        runtime_state.rotator = orig_rotator
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client


@pytest.mark.asyncio
async def test_auth_403_five_stage_retry_mechanism():
    """验证 403 阶梯重试机制：前 2 次重放，后 2 次重签，最后 1 次切号。"""
    from unittest.mock import AsyncMock, patch

    from fastapi import HTTPException

    from aistudio_api.application.api_service_gemini import Auth403RetryManager
    from aistudio_api.domain.errors import AuthError
    from aistudio_api.infrastructure.gateway.client import PreparedRequest

    manager = Auth403RetryManager()
    mock_client = MagicMock(spec=AIStudioClient)
    mock_prep = PreparedRequest(
        url="https://test",
        headers={"User-Agent": "test"},
        body="{}",
        model="models/gemini-3.8-flash",
    )
    manager.current_prepared = mock_prep

    err = AuthError("Permission denied")

    # 尝试 1 (第 1 次 403): 重放 1
    should_retry = await manager.handle_403(
        err, model="gemini-3.8-flash", client=mock_client
    )
    assert should_retry is True
    assert manager.count == 1
    assert manager.current_prepared is mock_prep

    # 尝试 2 (第 2 次 403): 重放 2
    should_retry = await manager.handle_403(
        err, model="gemini-3.8-flash", client=mock_client
    )
    assert should_retry is True
    assert manager.count == 2
    assert manager.current_prepared is mock_prep

    # 尝试 3 (第 3 次 403): 重新签名 1 (current_prepared 置为 None 重新签名)
    should_retry = await manager.handle_403(
        err, model="gemini-3.8-flash", client=mock_client
    )
    assert should_retry is True
    assert manager.count == 3
    assert manager.current_prepared is None

    # 尝试 4 (第 4 次 403): 重新签名 2
    should_retry = await manager.handle_403(
        err, model="gemini-3.8-flash", client=mock_client
    )
    assert should_retry is True
    assert manager.count == 4
    assert manager.current_prepared is None

    # 尝试 5 (第 5 次 403): 切号
    with patch(
        "aistudio_api.application.api_service_gemini.try_switch_account",
        new_callable=AsyncMock,
    ) as mock_switch:
        mock_switch.return_value = True
        should_retry = await manager.handle_403(
            err, model="gemini-3.8-flash", client=mock_client
        )
        assert should_retry is True
        assert manager.count == 5
        assert manager.current_prepared is None
        mock_switch.assert_awaited_once()

    # 尝试 6 (超过 5 次): 抛出 403 HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await manager.handle_403(err, model="gemini-3.8-flash", client=mock_client)
    assert exc_info.value.status_code == 403


async def _prepare_drifting_account_runtime(tmp_path, monkeypatch):
    """建立有效 Cookie 会话，并允许测试单独改变注册表模拟已有漂移。"""
    from aistudio_api.application import account_orchestrator

    store = AccountStore(accounts_dir=tmp_path / "accounts")
    service = AccountService(store)
    first = service.save_account_from_cookies(
        name="会话账号",
        email="first@example.test",
        storage_state={"cookies": [{"name": "SID", "value": "first-cookie"}]},
        cookie_id="generation-first",
    )
    second = service.save_account_from_cookies(
        name="目标账号",
        email="second@example.test",
        storage_state={"cookies": [{"name": "SID", "value": "second-cookie"}]},
        cookie_id="generation-second",
    )
    session = GatewaySession()
    await service.activate_account(first.id, session)
    client = MagicMock(spec=AIStudioClient)
    client.session = session
    rotator = AccountRotator(store)
    monkeypatch.setattr(runtime_state, "account_service", service)
    monkeypatch.setattr(runtime_state, "client", client)
    monkeypatch.setattr(runtime_state, "rotator", rotator)
    monkeypatch.setattr(account_orchestrator, "_last_switch_time", 0.0)
    return service, session, rotator, first, second


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [0, 1])
@pytest.mark.parametrize("disable_previous", [False, True])
async def test_ensure_active_account_rebinds_drifted_session(
    tmp_path, monkeypatch, attempt, disable_previous
):
    """每次尝试都必须将实际会话同步到注册表目标，包括禁用触发的回退。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    if disable_previous:
        service.toggle_account_enabled(first.id, False)
        current = service.get_active_account()
        assert current is not None and current.id == second.id
    else:
        service.set_active_account(second.id)

    await ensure_active_account(attempt=attempt, model="gemini-2.5-flash")

    context = session.active_context
    assert context is not None
    assert context.auth_file == str(service.get_account_auth_path(second.id))
    assert context.cookie_id == second.cookie_id
    assert context.cookies["SID"] == "second-cookie"


@pytest.mark.asyncio
async def test_try_switch_rebinds_metadata_fallback_before_reusing(
    tmp_path, monkeypatch
):
    """注册表回退不等于已完成切号，返回成功前必须实际交接凭据。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    service.toggle_account_enabled(first.id, False)
    current = service.get_active_account()
    assert current is not None and current.id == second.id

    assert await try_switch_account(
        model="gemini-2.5-flash", failed_account_id=first.id
    )

    context = session.active_context
    assert context is not None
    assert context.auth_file == str(service.get_account_auth_path(second.id))
    assert context.cookie_id == second.cookie_id


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [0, 1])
@pytest.mark.parametrize("unavailable_reason", ["disabled", "expired"])
async def test_no_available_accounts_clear_session_and_block_attempt(
    tmp_path, monkeypatch, attempt, unavailable_reason
):
    """全部禁用或失效时立即拒绝新尝试，不能继续保留旧就绪会话。"""
    from fastapi import HTTPException

    service, session, rotator, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    for account in (first, second):
        if unavailable_reason == "disabled":
            service.toggle_account_enabled(account.id, False)
        else:
            rotator.record_session_expired(account.id)

    with pytest.raises(HTTPException) as exc_info:
        await ensure_active_account(attempt=attempt, model="gemini-2.5-flash")

    assert exc_info.value.status_code == 503
    assert session.is_ready is False
    assert session.active_context is None or not session.active_context.is_usable()


@pytest.mark.asyncio
async def test_try_switch_without_candidates_clears_disabled_session(
    tmp_path, monkeypatch
):
    """无备用账号的失败切号必须清除已有的禁用会话。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    for account in (first, second):
        service.toggle_account_enabled(account.id, False)

    assert not await try_switch_account(failed_account_id=first.id)
    assert session.is_ready is False


@pytest.mark.asyncio
async def test_concurrent_reuse_synchronizes_drifted_session_once(
    tmp_path, monkeypatch
):
    """并发复用先实际修复漂移，再复用具体会话，避免级联重复激活。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    service.set_active_account(second.id)
    activation = AsyncMock(wraps=service.activate_account)
    monkeypatch.setattr(service, "activate_account", activation)

    results = await asyncio.gather(
        *(try_switch_account(failed_account_id=first.id) for _ in range(5))
    )

    assert all(results)
    assert activation.await_count == 1
    assert session.active_context is not None
    assert session.active_context.auth_file == str(
        service.get_account_auth_path(second.id)
    )


@pytest.mark.asyncio
async def test_concurrent_reuse_keeps_already_bound_session(tmp_path, monkeypatch):
    """已绑定到健康账号的并发请求可直接复用，无须重新激活。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    await service.activate_account(second.id, session)
    original_context = session.active_context
    activation = AsyncMock(wraps=service.activate_account)
    monkeypatch.setattr(service, "activate_account", activation)

    results = await asyncio.gather(
        *(try_switch_account(failed_account_id=first.id) for _ in range(5))
    )

    assert all(results)
    activation.assert_not_awaited()
    assert session.active_context is original_context


@pytest.mark.asyncio
async def test_stale_cookie_result_rebinds_current_generation(tmp_path, monkeypatch):
    """忽略旧认证结果时，成功重试仍必须使用刚导入的 Cookie 代际。"""
    service, session, rotator, _, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    await service.activate_account(second.id, session)
    previous_generation = second.cookie_id
    imported = service.save_account_from_cookies(
        name=second.name,
        email=second.email,
        account_id=second.id,
        storage_state={"cookies": [{"name": "SID", "value": "reimported-cookie"}]},
        cookie_id="generation-reimported",
    )

    assert await try_switch_account(
        failed_account_id=second.id,
        expected_cookie_id=previous_generation,
    )

    assert session.active_context is not None
    assert session.active_context.cookie_id == imported.cookie_id
    assert session.active_context.cookies["SID"] == "reimported-cookie"
    stats = rotator.get_account_stats(second.id)
    assert stats is not None and stats.auth_errors == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [0, 1])
@pytest.mark.parametrize("metadata_change", ["auth_user", "tier"])
async def test_ensure_active_account_refreshes_changed_metadata(
    tmp_path, monkeypatch, attempt, metadata_change
):
    """凭据路径和代际不变时，登录索引或权益变化仍必须更新请求会话。"""
    service, session, _, first, _ = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    original_context = session.active_context
    assert original_context is not None
    if metadata_change == "auth_user":
        service.save_account_from_cookies(
            name=first.name,
            email=first.email,
            account_id=first.id,
            storage_state={"cookies": [{"name": "SID", "value": "first-cookie"}]},
            cookie_id=first.cookie_id,
            auth_user="1",
        )
    else:
        assert service.update_account_tier(first.id, "Pro")

    await ensure_active_account(attempt=attempt, model="gemini-2.5-flash")

    account = service.get_active_account()
    context = session.active_context
    assert account is not None and context is not None
    assert context is not original_context
    assert context.auth_file == original_context.auth_file
    assert context.cookie_id == original_context.cookie_id
    assert context.auth_user == account.auth_user
    assert context.tier == account.tier


@pytest.mark.asyncio
async def test_waiting_expired_request_records_original_account_before_concurrent_reuse(
    tmp_path, monkeypatch
):
    """A 的失效结果等锁期间切到 B，仍须隔离 A 并保留 B 的就绪会话。"""
    import time

    from aistudio_api.application import account_orchestrator

    service, session, rotator, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    async with account_orchestrator._get_switch_lock():
        pending = asyncio.create_task(
            try_switch_account(
                model="gemini-3.8-flash",
                failed_account_id=first.id,
                is_session_expired=True,
                expected_cookie_id=first.cookie_id,
            )
        )
        await asyncio.sleep(0)
        assert not pending.done()
        await service.activate_account(second.id, session)
        context = session.active_context
        monkeypatch.setattr(account_orchestrator, "_last_switch_time", time.time())

    assert await asyncio.wait_for(pending, timeout=0.5)
    first_stats = rotator.get_account_stats(first.id)
    second_stats = rotator.get_account_stats(second.id)
    assert first_stats is not None and first_stats.session_expired
    assert second_stats is not None and not second_stats.session_expired
    assert session.active_context is context


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_credentials", ["missing_auth", "empty_cookies"])
async def test_invalid_current_credentials_fail_over_to_healthy_account(
    tmp_path, monkeypatch, invalid_credentials
):
    """当前账号无法绑定时仍应使用健康备用账号，测试全程保留凭据文件。"""
    service, session, _, first, second = await _prepare_drifting_account_runtime(
        tmp_path, monkeypatch
    )
    if invalid_credentials == "missing_auth":
        original_auth_path = service.get_account_auth_path
        monkeypatch.setattr(
            service,
            "get_account_auth_path",
            lambda account_id: (
                None if account_id == first.id else original_auth_path(account_id)
            ),
        )
    else:
        service.save_account_from_cookies(
            name=first.name,
            email=first.email,
            account_id=first.id,
            storage_state={"cookies": []},
            cookie_id="empty-cookie-generation",
        )

    await ensure_active_account(attempt=0, model="gemini-3.8-flash")

    account = service.get_active_account()
    context = session.active_context
    assert account is not None and account.id == second.id
    assert context is not None
    assert context.auth_file == str(service.get_account_auth_path(second.id))
    assert context.cookies["SID"] == "second-cookie"
