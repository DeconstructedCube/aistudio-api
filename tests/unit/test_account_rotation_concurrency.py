"""Unit tests for account rotation, concurrency protections, and per-model rate limiting."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application.account_orchestrator import (
    try_switch_account,
)
from aistudio_api.application.account_rotator import (
    AccountRotator,
    AccountStats,
    get_seconds_until_pacific_midnight,
)
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.gateway.client import (
    AIStudioClient,
    PreparedRequest,
)
from aistudio_api.infrastructure.gateway.session import (
    GENERATE_CONTENT_URL,
    GatewaySession,
    SessionContext,
)


async def _rotation_runtime(tmp_path, monkeypatch, *, account_count=2):
    """建立真实注册表、凭据文件和网关会话，保留并发切号的绑定检查。"""
    from aistudio_api.api.state import runtime_state
    from aistudio_api.application import account_orchestrator

    store = AccountStore(accounts_dir=tmp_path / "rotation_accounts")
    service = AccountService(store)
    accounts = [
        service.save_account_from_cookies(
            name=f"并发账号 {index}",
            email=f"rotation-{index}@example.test",
            storage_state={
                "cookies": [{"name": "SID", "value": f"synthetic_sid_{index}"}]
            },
            cookie_id=f"generation-{index}",
            tier="Pro",
        )
        for index in range(account_count)
    ]
    session = GatewaySession()
    await service.activate_account(accounts[0].id, session)
    client = MagicMock(spec=AIStudioClient)
    client.session = session
    rotator = AccountRotator(store)
    monkeypatch.setattr(runtime_state, "account_service", service)
    monkeypatch.setattr(runtime_state, "rotator", rotator)
    monkeypatch.setattr(runtime_state, "client", client)
    monkeypatch.setattr(account_orchestrator, "_last_switch_time", 0.0)
    return service, session, rotator, accounts


@pytest.mark.asyncio
async def test_prepare_request_concurrency():
    """并发构建请求时，每次请求均实时生成全新的 WAA 快照签名。"""
    mock_session = MagicMock(spec=GatewaySession)
    mock_session.ensure_ready = AsyncMock(return_value=SessionContext(auth_user="0"))
    mock_session.generate_snapshot = AsyncMock(return_value="!mock_snap")

    client = AIStudioClient()
    client._session = mock_session

    tasks = [
        client.prepare_request(prompt=f"hi {i}", model="gemini-2.5-flash")
        for i in range(5)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 5
    assert mock_session.generate_snapshot.call_count == 5
    for res in results:
        assert isinstance(res, PreparedRequest)
        assert res.url == GENERATE_CONTENT_URL

    res6 = await client.prepare_request(prompt="hi again", model="gemini-2.5-flash")
    assert mock_session.generate_snapshot.call_count == 6
    assert res6 is not None


@pytest.mark.asyncio
async def test_prepare_request_ephemeral_signature_identical_prompts():
    """相同 Prompt 并发请求时，每次都生成全新快照签名，绝不复用。"""
    mock_session = MagicMock(spec=GatewaySession)
    mock_session.ensure_ready = AsyncMock(return_value=SessionContext(auth_user="0"))
    snap_counter = 0

    async def fake_snapshot(contents, *args, **kwargs):
        nonlocal snap_counter
        snap_counter += 1
        return f"!snap_token_{snap_counter}"

    mock_session.generate_snapshot = AsyncMock(side_effect=fake_snapshot)
    client = AIStudioClient()
    client._session = mock_session

    tasks = [
        client.prepare_request(prompt="same question", model="gemini-2.5-flash")
        for _ in range(3)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 3
    snapshots = [r.snapshot for r in results if r]
    assert snapshots == ["!snap_token_1", "!snap_token_2", "!snap_token_3"]
    assert len(set(snapshots)) == 3


@pytest.mark.asyncio
async def test_account_stats_per_model_cooldown():
    """测试账号在特定模型上的 429 冷却与隔离。"""
    stats = AccountStats(account_id="acc_1")
    assert stats.is_available("gemini-2.5-pro")
    assert stats.is_available("gemini-2.5-flash")

    # 对 pro 模型限流
    stats.record_rate_limited(model="gemini-2.5-pro")
    assert not stats.is_available("gemini-2.5-pro")
    # flash 模型依然可用！
    assert stats.is_available("gemini-2.5-flash")
    assert stats.get_cooldown_remaining("gemini-2.5-pro") > 0
    assert stats.get_cooldown_remaining("gemini-2.5-flash") == 0

    # 成功请求清除偶发冷却
    stats.record_success(model="gemini-2.5-pro")
    assert stats.is_available("gemini-2.5-pro")


@pytest.mark.asyncio
async def test_account_stats_pacific_midnight_reset():
    """测试美西跨天日限额自动恢复。"""
    stats = AccountStats(account_id="acc_1")
    m_stats = stats.get_model_daily("gemini-2.5-pro")
    m_stats.cooldown_until = time.time() + 3600
    stats.daily_date_la = "2020-01-01"  # 过去的日期

    assert stats.is_available("gemini-2.5-pro")
    assert stats.daily_date_la != "2020-01-01"


@pytest.mark.asyncio
async def test_account_stats_clear_cooldown_resets_counter():
    """测试手动清除冷却会重置 429 次数计数器，不会在下一次 429 时直接秒锁至午夜。"""
    stats = AccountStats(account_id="acc_1")
    # 连续 3 次 429 触发当日锁定
    stats.record_rate_limited(model="gemini-2.5-pro")
    stats.record_rate_limited(model="gemini-2.5-pro")
    stats.record_rate_limited(model="gemini-2.5-pro")
    assert not stats.is_available("gemini-2.5-pro")
    assert stats.get_cooldown_remaining("gemini-2.5-pro") > 60.0

    # 手动清除冷却
    stats.clear_cooldown(model="gemini-2.5-pro")
    assert stats.is_available("gemini-2.5-pro")
    m_stats = stats.get_model_daily("gemini-2.5-pro")
    assert m_stats.rate_limited == 0

    # 下一次遇到 429 应重新享有 60s 临时冷却，而不是直接锁到午夜
    stats.record_rate_limited(model="gemini-2.5-pro")
    rem = stats.get_cooldown_remaining("gemini-2.5-pro")
    assert 0 < rem <= 60.0


@pytest.mark.asyncio
async def test_rotator_sticky_mode():
    """测试 Sticky 模式：默认保持当前号，直到限流才切换。"""
    store = MagicMock(spec=AccountStore)
    acc1 = AccountMeta(
        id="acc_1", name="Account 1", email="acc1@example.com", created_at="2026-01-01"
    )
    acc2 = AccountMeta(
        id="acc_2", name="Account 2", email="acc2@example.com", created_at="2026-01-01"
    )
    store.list_accounts.return_value = [acc1, acc2]

    rotator = AccountRotator(account_store=store)

    # 初始获取账号，获取 acc1
    next_acc = await rotator.get_next_account(
        model="gemini-2.5-pro", current_account_id=acc1.id
    )
    assert next_acc is not None
    assert next_acc.id == "acc_1"

    # 请求 flash 模型，继续复用 acc1 (sticky)
    next_acc = await rotator.get_next_account(
        model="gemini-2.5-flash", current_account_id=acc1.id
    )
    assert next_acc is not None
    assert next_acc.id == "acc_1"

    # acc1 在 pro 模型上发生 429
    rotator.record_rate_limited("acc_1", model="gemini-2.5-pro")

    # 请求 flash 模型，acc1 依然可用，继续复用 acc1！
    next_acc = await rotator.get_next_account(
        model="gemini-2.5-flash", current_account_id=acc1.id
    )
    assert next_acc is not None
    assert next_acc.id == "acc_1"

    # 请求 pro 模型，acc1 不可用，自动切换到 acc2！
    next_acc = await rotator.get_next_account(
        model="gemini-2.5-pro", current_account_id=acc1.id
    )
    assert next_acc is not None
    assert next_acc.id == "acc_2"

    # 随后请求 flash 或 pro，均以 acc2 为 sticky 目标
    next_acc = await rotator.get_next_account(
        model="gemini-2.5-flash", current_account_id=acc2.id
    )
    assert next_acc is not None
    assert next_acc.id == "acc_2"


@pytest.mark.asyncio
async def test_rotator_auth_error_failover():
    """测试 403 鉴权异常时，立即故障转移切换至健康账号，并在后续请求中自动规避故障账号。"""
    store = MagicMock(spec=AccountStore)
    acc1 = AccountMeta(
        id="acc_1", name="Account 1", email="acc1@example.com", created_at="2026-01-01"
    )
    acc2 = AccountMeta(
        id="acc_2", name="Account 2", email="acc2@example.com", created_at="2026-01-01"
    )
    store.list_accounts.return_value = [acc1, acc2]

    rotator = AccountRotator(account_store=store)

    # acc1 发生 403 / The caller does not have permission
    rotator.record_auth_error("acc_1", model="gemini-3.8-flash")

    # 尝试切号：明确指定 failed_account_id=acc_1，应当秒级切换到 acc_2
    next_acc = await rotator.get_next_account(
        model="gemini-3.8-flash",
        current_account_id=acc1.id,
        failed_account_id=acc1.id,
    )
    assert next_acc is not None
    assert next_acc.id == "acc_2"

    # 新进请求（未指定 failed_account_id）也因 acc_1 具有 auth_errors 惩罚分 (+200) 而自动优先选择健康的 acc_2
    new_req_acc = await rotator.get_next_account(
        model="gemini-3.8-flash",
        current_account_id=None,
    )
    assert new_req_acc is not None
    assert new_req_acc.id == "acc_2"


@pytest.mark.asyncio
async def test_try_switch_account_avalanche_protection(tmp_path, monkeypatch):
    """并发故障转移只激活一次，并实际绑定备用账号的凭据。"""
    service, session, _, accounts = await _rotation_runtime(tmp_path, monkeypatch)
    first, second = accounts
    started, finish = asyncio.Event(), asyncio.Event()
    original_activate = service.activate_account

    async def activate(account_id, gateway_session):
        started.set()
        await finish.wait()
        return await original_activate(account_id, gateway_session)

    activation = AsyncMock(side_effect=activate)
    monkeypatch.setattr(service, "activate_account", activation)
    tasks = [
        asyncio.create_task(
            try_switch_account(model="gemini-2.5-pro", failed_account_id=first.id)
        )
        for _ in range(3)
    ]
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert all(not task.done() for task in tasks)
        finish.set()
        assert all(await asyncio.gather(*tasks))
        activation.assert_awaited_once_with(second.id, session)
        assert session.active_context is not None
        assert session.active_context.auth_file == str(
            service.get_account_auth_path(second.id)
        )
        assert session.active_context.cookie_id == second.cookie_id
    finally:
        finish.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_get_seconds_until_pacific_midnight():
    """测试距离美西 0 点剩余秒数计算。"""
    remaining = get_seconds_until_pacific_midnight()
    assert 0 <= remaining <= 86400


@pytest.mark.asyncio
async def test_runtime_state_record_model_stats():
    """测试 RuntimeState.record 正确更新 stats，不抛出 TypeError 'Field' object is not subscriptable。"""
    from aistudio_api.api.state import RuntimeState

    state = RuntimeState()
    state.record("models/gemini-3.8-flash", "errors")
    state.record(
        "models/gemini-3.8-flash",
        "success",
        {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )
    state.record("models/gemini-3.8-flash", "rate_limited")

    item = state.model_stats["models/gemini-3.8-flash"]
    assert item.requests == 3
    assert item.errors == 1
    assert item.success == 1
    assert item.rate_limited == 1
    assert item.total_tokens == 30
    assert item.last_used is not None


@pytest.mark.asyncio
async def test_prepare_request_model_preservation():
    """测试构建请求时模型名称准确写入顶层 wire 数组。"""
    mock_session = MagicMock(spec=GatewaySession)
    mock_session.ensure_ready = AsyncMock(return_value=SessionContext(auth_user="0"))
    mock_session.generate_snapshot = AsyncMock(return_value="!mock_snap")
    client = AIStudioClient()
    client._session = mock_session

    prepared = await client.prepare_request(
        prompt="Hello",
        model="models/gemini-3.8-flash",
    )
    assert prepared is not None
    assert prepared.model == "models/gemini-3.8-flash"

    body = json.loads(prepared.body)
    assert body[0] == "models/gemini-3.8-flash"


@pytest.mark.asyncio
async def test_rotator_prioritizes_least_used_account():
    """测试轮询机制优先使用使用次数少的账号，同时在当前号健康时保持黏性（减少切换）。"""
    store = MagicMock(spec=AccountStore)
    acc1 = AccountMeta(
        id="acc_1", name="Account 1", email="acc1@test.com", created_at="2026-01-01"
    )
    acc2 = AccountMeta(
        id="acc_2", name="Account 2", email="acc2@test.com", created_at="2026-01-01"
    )
    store.list_accounts.return_value = [acc1, acc2]

    rotator = AccountRotator(account_store=store)
    # acc1 调用了 50 次，acc2 只调用了 5 次 (使用公共 record_success 接口填充状态)
    for _ in range(15):
        rotator.record_success("acc_1", model="gemini-3.8-flash")
    for _ in range(5):
        rotator.record_success("acc_2", model="gemini-3.8-flash")
    # 1. 减少切换（黏性优先）：如果当前活跃号是 acc1 且健康可用，继续复用 acc1
    chosen = await rotator.get_next_account(
        model="models/gemini-3.8-flash",
        current_account_id="acc_1",
    )
    assert chosen is not None
    assert chosen.id == "acc_1"
    # 2. 发生故障转移（如 acc1 触发 429）或初次调度时：优先选择调用次数少的 acc2
    chosen_failover = await rotator.get_next_account(
        model="models/gemini-3.8-flash",
        current_account_id="acc_1",
        failed_account_id="acc_1",
    )
    assert chosen_failover is not None
    assert chosen_failover.id == "acc_2"

    rotator.record_success("acc_2", model="models/gemini-3.8-flash")
    acc2_stats = rotator.get_account_stats("acc_2")
    assert acc2_stats is not None
    assert acc2_stats.get_model_daily("gemini-3.8-flash").requests == 6


@pytest.mark.asyncio
async def test_model_requests_midnight_reset():
    stats = AccountStats(account_id="acc_1")
    m_stats = stats.get_model_daily("gemini-3.8-flash")
    m_stats.requests = 50
    m_stats.cooldown_until = time.time() + 3600
    stats.daily_date_la = "2020-01-01"  # 过去的日期

    # 跨天后自动恢复可用且单日请求计数清零
    assert stats.is_available("gemini-3.8-flash")
    assert stats.get_model_daily("gemini-3.8-flash").requests == 0


@pytest.mark.asyncio
async def test_ensure_authorization_header_concurrency_and_fallback():
    """并发请求或异常时，_ensure_authorization_header 绝不丢失已有 Authorization 头部。"""
    from aistudio_api.infrastructure.gateway.transport import (
        _ensure_authorization_header,
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": "SAPISIDHASH 1789397237_abcdef0123456789",
    }

    result = await _ensure_authorization_header(headers, auth_user="0")
    assert "Authorization" in result
    assert result["Authorization"] == "SAPISIDHASH 1789397237_abcdef0123456789"
    assert result["X-Goog-AuthUser"] == "0"


@pytest.mark.asyncio
async def test_try_switch_account_concurrent_403_avalanche_and_in_place_recovery(
    tmp_path, monkeypatch
):
    """并发 403 只刷新一次真实会话，刷新后清除实际账号的认证处罚。"""
    service, session, rotator, accounts = await _rotation_runtime(
        tmp_path, monkeypatch, account_count=1
    )
    account = accounts[0]
    old_context = session.active_context
    rotator.record_auth_error(account.id, model="gemini-3.8-flash")
    stats = rotator.get_account_stats(account.id)
    assert stats is not None and stats.auth_errors == 1
    started, finish = asyncio.Event(), asyncio.Event()
    original_activate = service.activate_account

    async def activate(account_id, gateway_session):
        started.set()
        await finish.wait()
        return await original_activate(account_id, gateway_session)

    activation = AsyncMock(side_effect=activate)
    monkeypatch.setattr(service, "activate_account", activation)
    tasks = [
        asyncio.create_task(
            try_switch_account(model="gemini-3.8-flash", failed_account_id=account.id)
        )
        for _ in range(3)
    ]
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert all(not task.done() for task in tasks)
        finish.set()
        assert all(await asyncio.gather(*tasks))
        activation.assert_awaited_once_with(account.id, session)
        assert session.active_context is not old_context
        assert session.active_context is not None
        assert session.active_context.cookie_id == account.cookie_id
        assert stats.is_available("gemini-3.8-flash")
        assert stats.auth_errors == 0
    finally:
        finish.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
