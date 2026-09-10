"""Unit tests for BrowserSession with CDP backend."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.anyio
async def test_browser_session_generate_snapshot(mock_cdp_page):
    session = BrowserSession(port=9222)
    session._page = mock_cdp_page
    session._snap_key = "test_snapshot_fn"

    async def fake_evaluate(expr, *args, **kwargs):
        if "window.default_MakerSuite" in expr or "window.__bg_hooked" in expr:
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


@pytest.mark.anyio
async def test_browser_session_send_hooked_request(mock_cdp_page):
    session = BrowserSession(port=9222)
    session._page = mock_cdp_page
    session._templates["test_model"] = {
        "url": "https://alkalimakersuite-pa.clients6.google.com/test",
        "headers": {"content-type": "application/json"},
    }

    async def fake_evaluate(expr, args=None, *a, **kw):
        if "window.default_MakerSuite" in expr or "window.__bg_hooked" in expr:
            return "already_hooked"
        if "!window.__bg_service" in expr:
            return True
        if "XMLHttpRequest" in expr:
            return {"status": 200, "body": '["response_data"]'}
        return None

    mock_cdp_page.evaluate.side_effect = fake_evaluate

    status, body = await session.send_hooked_request(body='["test"]', timeout_ms=5000)
    assert status == 200
    assert body == b'["response_data"]'


@pytest.mark.anyio
async def test_browser_session_send_streaming_request(mock_cdp_page):
    session = BrowserSession(port=9222)
    session._page = mock_cdp_page
    session._templates["test_model"] = {
        "url": "https://alkalimakersuite-pa.clients6.google.com/stream",
        "headers": {"content-type": "application/json"},
    }

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
        if "(rid) => window.__stream_next" in expr:
            if events:
                return events.pop(0)
            return {"type": "done"}
        if "STREAMING_INIT" in expr or "window.__streams" in expr:
            return None
        return None

    mock_cdp_page.evaluate.side_effect = fake_evaluate

    collected = []
    async for tag, data in session.send_streaming_request(body='["stream"]', timeout_ms=5000):
        collected.append((tag, data))

    assert ("status", 200) in collected
    assert ("chunk", b"data: hello ") in collected
    assert ("chunk", b"world\n\n") in collected
