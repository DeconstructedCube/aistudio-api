"""Unit tests for BrowserSession with CDP backend."""

from __future__ import annotations

import json
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart


@pytest.fixture
def mock_cdp_page():
    page = MagicMock(spec=CDPPage)
    page.url = "https://aistudio.google.com/prompts/new_chat"
    page.is_closed = MagicMock(return_value=False)
    page.goto = AsyncMock()
    page.evaluate = AsyncMock()
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=True)
    page.send_control_enter = AsyncMock(return_value=True)
    page.get_cookies = AsyncMock(return_value=[])
    page.set_cookies = AsyncMock()
    page.on_request = MagicMock(return_value=lambda: None)
    page.on_response = MagicMock(return_value=lambda: None)
    return page


@pytest.mark.asyncio
async def test_browser_session_generate_snapshot(mock_cdp_page):
    session = BrowserSession(port=9222)
    session._page = mock_cdp_page
    session._snap_key = "test_snapshot_fn"

    async def fake_evaluate(expr, *args, **kwargs):
        if "Promise.resolve(result)" in expr or "Promise.resolve(dms[snapKey]" in expr:
            return "!mocked_snapshot_token_value_123"
        if "window.__bg_hooked" in expr or "return 'already_hooked'" in expr:
            return "already_hooked"
        if "!window.__bg_service" in expr:
            return True
        if "window.__sl" in expr:
            return 100
        if "window.__sr" in expr:
            return "!mocked_snapshot_token_value_123"
        return None

    mock_cdp_page.evaluate.side_effect = fake_evaluate

    contents = [
        AistudioContent(
            role="user",
            parts=[AistudioPart(text="hello world")],
        )
    ]
    snapshot = await session.generate_snapshot(contents)
    assert snapshot == "!mocked_snapshot_token_value_123"


@pytest.mark.asyncio
async def test_browser_session_send_hooked_request(mock_cdp_page):
    session = BrowserSession(port=9222)
    session._page = mock_cdp_page

    async def fake_evaluate(expr, args=None, *a, **kw):
        if "window.default_MakerSuite" in expr or "window.__bg_hooked" in expr:
            return "already_hooked"
        if "!window.__bg_service" in expr:
            return True
        if "XMLHttpRequest" in expr or "fetch(" in expr or "fetch" in expr:
            return {"status": 200, "body": '["response_data"]'}
        return None

    mock_cdp_page.evaluate.side_effect = fake_evaluate

    status, body = await session.send_hooked_request(
        url="https://alkalimakersuite-pa.clients6.google.com/test",
        headers={"content-type": "application/json"},
        body='["test"]',
        timeout_ms=5000,
    )
    assert status == 200
    assert body == b'["response_data"]'


@pytest.mark.asyncio
async def test_browser_session_send_streaming_request(mock_cdp_page):
    session = BrowserSession(port=9222)
    session._page = mock_cdp_page

    binding_callback = None

    def mock_on_binding(name, cb):
        nonlocal binding_callback
        if name == "__aistudio_stream_push__":
            binding_callback = cb

    mock_cdp_page.on_binding = MagicMock(side_effect=mock_on_binding)

    events = [
        {"type": "status", "status": 200},
        {"type": "chunk", "text": "data: hello "},
        {"type": "chunk", "text": "world\n\n"},
        {"type": "done"},
    ]

    async def fake_evaluate(expr, args=None, *a, **kw):
        if "window.default_MakerSuite" in expr or "window.__bg_hooked" in expr:
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

    mock_cdp_page.evaluate.side_effect = fake_evaluate

    collected = []
    async for tag, data in session.send_streaming_request(
        url="https://alkalimakersuite-pa.clients6.google.com/stream",
        headers={"content-type": "application/json"},
        body='["stream"]',
        timeout_ms=5000,
    ):
        collected.append((tag, data))

    assert ("status", 200) in collected
    assert ("chunk", b"data: hello ") in collected
    assert ("chunk", b"world\n\n") in collected


@pytest.mark.asyncio
async def test_browser_session_liveness_and_auto_reconnect():
    """Test BrowserSession detects dead page and auto-reconnects."""
    session = BrowserSession(port=9222)
    mock_dead_page = MagicMock(spec=CDPPage)
    mock_dead_page.is_closed = MagicMock(return_value=False)
    mock_dead_page.is_alive = AsyncMock(return_value=False)

    mock_alive_page = MagicMock(spec=CDPPage)
    mock_alive_page.is_closed = MagicMock(return_value=False)
    mock_alive_page.is_alive = AsyncMock(return_value=True)

    session._page = mock_dead_page
    session._ensure_browser_cdp = AsyncMock(return_value=mock_alive_page)
    session._close_internal = AsyncMock()

    # ensure_context should detect dead page, close it, and reconnect to new page
    page = await session.ensure_context()
    assert page == mock_alive_page
    session._close_internal.assert_called_once()
    session._ensure_browser_cdp.assert_called_once()


@pytest.mark.asyncio
async def test_generate_snapshot_hash_complex_tools_and_multimodal():
    """Verify that hash calculation accurately extracts parts according to _.Nv(request)."""
    session = BrowserSession(port=9222)
    mock_page = MagicMock(spec=CDPPage)
    mock_page.url = "https://aistudio.google.com/prompts/new_chat"
    mock_page.is_closed = MagicMock(return_value=False)

    passed_args = []

    async def fake_evaluate(expr, args=None, *a, **kw):
        if (
            "INSTALL_HOOKS" in expr
            or "window.__bg_hooked" in expr
            or "return 'already_hooked'" in expr
        ):
            return "already_hooked"
        if "!window.__bg_service" in expr:
            return True
        if "Promise.resolve(result)" in expr:
            passed_args.append(args)
            return "!snapshot_token_ok"
        return None

    mock_page.evaluate.side_effect = fake_evaluate
    session._page = mock_page
    session._snap_key = "snap_fn"

    # Complex multi-turn contents with text, inline data, file_id, function_call, and function_response
    contents = [
        AistudioContent(
            role="user",
            parts=[
                AistudioPart(text="What is the weather in Tokyo?"),
                AistudioPart(inline_data=("image/png", "iVBORw0KGgoAAAANSUhEUg==")),
            ],
        ),
        AistudioContent(
            role="model",
            parts=[
                AistudioPart(
                    function_call=("get_weather", {"city": "Tokyo"}, "call_123"),
                    thought=True,
                ),
            ],
        ),
        AistudioContent(
            role="user",
            parts=[
                AistudioPart(
                    function_response=("get_weather", {"temp": "22C"}, "call_123")
                ),
                AistudioPart(file_id="files/sample_data_file"),
            ],
        ),
    ]

    expected_parts = [
        "What is the weather in Tokyo?",
        "iVBORw0KGgoAAAANSUhEUg==",
        "",  # function_call maps to ""
        "",  # function_response maps to ""
        "files/sample_data_file",
    ]
    expected_hash = sha256(" ".join(expected_parts).encode("utf-8")).hexdigest()

    snapshot = await session.generate_snapshot(contents)
    assert snapshot == "!snapshot_token_ok"
    assert passed_args == [expected_hash]


@pytest.mark.asyncio
async def test_account_service_activate_account_warms_up_browser():
    """Verify activate_account invokes ensure_botguard_service for instant readiness."""
    mock_store = MagicMock(spec=AccountStore)
    mock_store.get_account.return_value = AccountMeta(
        id="acc_1",
        name="Test Account",
        email="test@example.com",
        created_at="2026-09-01T00:00:00Z",
        last_used="2026-09-01T00:00:00Z",
        auth_user="0",
    )
    mock_store.get_auth_path_optional.return_value = "/data/accounts/acc_1/auth.json"

    mock_session = MagicMock(spec=BrowserSession)
    mock_session.switch_auth = AsyncMock()
    mock_session.ensure_botguard_service = AsyncMock()

    cache = SnapshotCache()
    service = AccountService(mock_store)

    result = await service.activate_account(
        account_id="acc_1",
        browser_session=mock_session,
        snapshot_cache=cache,
    )

    assert result is not None
    assert result.id == "acc_1"
    mock_session.switch_auth.assert_called_once_with("/data/accounts/acc_1/auth.json")
    mock_session.ensure_botguard_service.assert_called_once()


@pytest.mark.asyncio
async def test_capture_service_single_pass_full_payload():
    """Verify capture service builds full modified_body with system instruction and tools in one pass."""
    mock_session = MagicMock(spec=BrowserSession)
    mock_session.capture_template = AsyncMock(
        return_value={
            "url": "https://example.com/generate",
            "headers": {"content-type": "application/json"},
            "body": '["models/gemini-3.7-flash",[[[[null,"hi"]],"user"]],null,[null,null,null,128],"!orig"]',
        }
    )
    mock_session.generate_snapshot = AsyncMock(return_value="!fresh_snapshot_token")

    cache = SnapshotCache()
    capture_svc = RequestCaptureService(mock_session, cache)

    contents = [
        AistudioContent(
            role="user",
            parts=[AistudioPart(text="Hello from user")],
        )
    ]
    sys_inst = AistudioContent(
        role="user",
        parts=[AistudioPart(text="You are a helpful assistant")],
    )
    tools = [[[]]]  # code execution tool

    captured = await capture_svc.capture(
        prompt="Hello from user",
        model="models/gemini-3.7-flash",
        contents=contents,
        system_instruction_content=sys_inst,
        tools=tools,
        max_tokens=256,
        temperature=0.5,
    )

    assert captured is not None
    assert captured.snapshot == "!fresh_snapshot_token"
    assert "You are a helpful assistant" in captured.body
    assert "models/gemini-3.7-flash" in captured.body
