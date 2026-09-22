"""Unit tests for account rotation, concurrency protections, and per-model rate limiting."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aistudio_api.application.account_orchestrator import (
    try_switch_account,
)
from aistudio_api.application.account_rotator import (
    AccountRotator,
    AccountStats,
    get_seconds_until_pacific_midnight,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
    RequestCaptureService,
)
from aistudio_api.infrastructure.gateway.session import BrowserSession


@pytest.mark.asyncio
async def test_capture_service_concurrency_and_caching():
    """并发请求抓取模板时，模板捕获只执行 1 次，其余直接命中缓存。"""
    mock_session = MagicMock(spec=BrowserSession)
    mock_session.generate_snapshot = AsyncMock(return_value="mock_snap")

    call_count = 0

    async def fake_capture_template(model: str):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.001)
        return {
            "url": "https://example.com/generate",
            "headers": {"content-type": "application/json"},
            "body": '["models/gemini-2.5-flash",[[[[null,"old"]],"user"]],null,[null,null,null,128,0.5,0.8,16],"orig_snap"]',
        }

    mock_session.capture_template = AsyncMock(side_effect=fake_capture_template)
    cache = SnapshotCache(ttl=3600, max_size=100)
    service = RequestCaptureService(session=mock_session, snapshot_cache=cache)

    # 5 个并发请求同时请求同一个 model
    tasks = [
        service.capture(prompt=f"hi {i}", model="gemini-2.5-flash") for i in range(5)
    ]
    results = await asyncio.gather(*tasks)

    # capture_template 应当只被执行 1 次
    assert call_count == 1
    assert len(results) == 5
    for res in results:
        assert isinstance(res, CapturedRequest)
        assert res.url == "https://example.com/generate"

    # 再次请求，直接命中缓存，不会增加调用次数
    res6 = await service.capture(prompt="hi again", model="gemini-2.5-flash")
    assert call_count == 1
    assert res6 is not None


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
    stats.model_cooldowns["gemini-2.5-pro"] = time.time() + 3600
    stats.model_rate_limited_dates["gemini-2.5-pro"] = "2020-01-01"  # 过去的日期

    assert stats.is_available("gemini-2.5-pro")
    assert "gemini-2.5-pro" not in stats.model_rate_limited_dates



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
    assert "gemini-2.5-pro" not in stats.model_rate_limited

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

    # 新进请求（未指定 failed_account_id）也应该自动规避仍在 auth_cooldown 的 acc_1，直接选择 acc_2
    new_req_acc = await rotator.get_next_account(
        model="gemini-3.8-flash",
        current_account_id=None,
    )
    assert new_req_acc is not None
    assert new_req_acc.id == "acc_2"


@pytest.mark.asyncio
async def test_try_switch_account_avalanche_protection():
    """测试并发多个 429 时，_switch_lock 防止级联切号。"""
    from aistudio_api.api.state import runtime_state

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_acc_service = MagicMock()
    mock_client = MagicMock()
    mock_client._session = MagicMock()

    acc1 = AccountMeta(
        id="acc_1", name="Acc 1", email="acc1@test.com", created_at="2026-01-01"
    )
    acc2 = AccountMeta(
        id="acc_2", name="Acc 2", email="acc2@test.com", created_at="2026-01-01"
    )

    active_acc = acc1
    mock_acc_service.get_active_account.side_effect = lambda: active_acc

    stats_map = {
        "acc_1": AccountStats(account_id="acc_1"),
        "acc_2": AccountStats(account_id="acc_2"),
    }
    mock_rotator._stats = stats_map

    activate_count = 0

    async def fake_activate(acc_id, *args, **kwargs):
        nonlocal active_acc, activate_count
        activate_count += 1
        await asyncio.sleep(0.001)
        active_acc = acc2 if acc_id == "acc_2" else acc1
        return active_acc

    mock_acc_service.activate_account = AsyncMock(side_effect=fake_activate)
    mock_rotator.get_next_account = AsyncMock(return_value=acc2)

    with (
        patch.object(runtime_state, "rotator", mock_rotator),
        patch.object(runtime_state, "account_service", mock_acc_service),
        patch.object(runtime_state, "client", mock_client),
    ):
        # 3 个并发协程同时遇到 429 并尝试切号
        tasks = [
            try_switch_account(model="gemini-2.5-pro", failed_account_id="acc_1")
            for _ in range(3)
        ]
        results = await asyncio.gather(*tasks)

        assert all(results)
        # 只应该发生 1 次实质性的 activate_account 调用
        assert activate_count == 1


def test_get_seconds_until_pacific_midnight():
    """测试距离美西 0 点剩余秒数计算。"""
    remaining = get_seconds_until_pacific_midnight()
    assert 0 <= remaining <= 86400


@pytest.mark.asyncio
async def test_goto_aistudio_net_err_aborted_tolerance():
    """当 page.goto 遭遇 net::ERR_ABORTED 但已在 aistudio 时，视为有效抵达并容错。"""
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage

    page = MagicMock(spec=CDPPage)
    page.url = "https://aistudio.google.com/prompts/new_chat"
    page.goto = AsyncMock(
        side_effect=RuntimeError("Navigation failed: net::ERR_ABORTED")
    )
    page.evaluate = AsyncMock(
        side_effect=lambda expr, *a, **kw: (
            "https://aistudio.google.com/prompts/new_chat"
            if "location.href" in expr
            else (True if "default_MakerSuite" in expr else None)
        )
    )
    page.wait_for_timeout = AsyncMock()

    session = BrowserSession(port=9222)
    session.get_current_auth_user = MagicMock(return_value="0")
    session._verify_account_identity = AsyncMock()
    session._save_cookies = AsyncMock()
    # 应该正常完成，不抛出 net::ERR_ABORTED 异常
    await session._goto_aistudio(page)
    assert session._verify_account_identity.called


@pytest.mark.asyncio
async def test_ensure_botguard_available_regions_fast_fail():
    """检测到 available-regions 时立刻抛出 RuntimeError，无需等待 20s 超时。"""
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage

    page = MagicMock(spec=CDPPage)
    page.url = "https://aistudio.google.com/available-regions"
    page.is_closed = MagicMock(return_value=False)
    page.evaluate = AsyncMock(return_value=False)

    session = BrowserSession(port=9222)
    session._page = page
    session.ensure_context = AsyncMock(return_value=page)
    session._install_hooks = AsyncMock()

    with pytest.raises(RuntimeError, match="地区限制"):
        await session.ensure_botguard_service()


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
async def test_capture_model_preservation_when_template_differs():
    """测试当 Hook 拦截模板为 3.7-flash 时，请求 3.8-flash 不会被模板模型覆盖。"""
    mock_session = MagicMock(spec=BrowserSession)
    mock_session.generate_snapshot = AsyncMock(return_value="!mock_snap")
    mock_session.capture_template = AsyncMock(
        return_value={
            "url": "https://example.com/generate",
            "headers": {"content-type": "application/json"},
            "body": '["models/gemini-3.7-flash",[[[[null,"template prompt"]],"user"]],null,[null,null,null,128,0.5,0.8,16],"old_snap"]',
        }
    )
    cache = SnapshotCache()
    service = RequestCaptureService(session=mock_session, snapshot_cache=cache)

    captured = await service.capture(
        prompt="Hello",
        model="models/gemini-3.8-flash",
    )
    assert captured is not None
    # 确认 captured.model 是用户请求的模型，而不是模板的 gemini-3.7-flash
    assert captured.model == "models/gemini-3.8-flash"
    import json

    body = json.loads(captured.body)
    assert body[0] == "models/gemini-3.8-flash"


@pytest.mark.asyncio
async def test_browser_session_send_streaming_batch_events():
    """测试流式回放支持批量事件以提升并发吞吐。"""
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage

    page = MagicMock(spec=CDPPage)
    page.url = "https://aistudio.google.com/prompts/new_chat"
    page.is_closed = MagicMock(return_value=False)
    binding_callback = None

    def mock_on_binding(name, cb):
        nonlocal binding_callback
        if name == "__aistudio_stream_push__":
            binding_callback = cb

    page.on_binding = MagicMock(side_effect=mock_on_binding)

    events = [
        {"type": "status", "status": 200},
        {"type": "chunk", "text": "hello "},
        {"type": "chunk", "text": "world"},
        {"type": "done"},
    ]

    async def fake_eval(expr, args=None, *a, **kwargs):
        if "default_MakerSuite" in expr or "window.__bg_hooked" in expr:
            return "already_hooked"
        if "window.__bg_service" in expr:
            return True
        if isinstance(args, dict) and "rid" in args and binding_callback:
            rid = args["rid"]
            for ev in events:
                ev_copy = dict(ev)
                ev_copy["rid"] = rid
                binding_callback(json.dumps(ev_copy))
            return None
        return None

    page.evaluate = AsyncMock(side_effect=fake_eval)
    session = BrowserSession(port=9222)
    session._page = page
    results = []
    async for tag, data in session.send_streaming_request(
        url="https://example.com",
        headers={},
        body="[]",
        timeout_ms=5000,
    ):
        results.append((tag, data))

    assert ("status", 200) in results
    assert ("chunk", b"hello ") in results
    assert ("chunk", b"world") in results
