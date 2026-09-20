"""Unit tests for BrowserSession with CDP backend."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPPage
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
