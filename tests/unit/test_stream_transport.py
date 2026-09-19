"""Unit tests for XHRStreamTransport and CDP native stream binding."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    STREAM_CLEANUP_JS,
    STREAMING_INIT_JS,
)
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport


def test_streaming_scripts_include_window_deletions():
    """Verify V8 memory cleanup logic is present in scripts."""
    assert "delete window.__streams[rid]" in STREAMING_INIT_JS
    assert "delete window.__stream_abort[rid]" in STREAMING_INIT_JS
    assert "delete window.__streams[rid]" in STREAM_CLEANUP_JS
    assert "delete window.__stream_abort[rid]" in STREAM_CLEANUP_JS


@pytest.mark.asyncio
async def test_transport_native_cdp_binding_stream_push():
    """Verify realtime stream event dispatch via CDP Runtime.bindingCalled."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    binding_listener = None

    def mock_on_binding(name: str, callback):
        nonlocal binding_listener
        if name == "__aistudio_stream_push__":
            binding_listener = callback

    page.on_binding = MagicMock(side_effect=mock_on_binding)
    page.add_binding = AsyncMock()

    captured_rid = None

    async def fake_evaluate(expr, args=None, *a, **kw):
        nonlocal captured_rid
        if isinstance(args, dict) and "rid" in args:
            captured_rid = args["rid"]
        return

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    async def push_events():
        while captured_rid is None or binding_listener is None:
            await asyncio.sleep(0.01)

        binding_listener(
            json.dumps({"rid": captured_rid, "type": "status", "status": 200})
        )
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "chunk", "text": "Hello "})
        )
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "chunk", "text": "CDP Binding!"})
        )
        binding_listener(json.dumps({"rid": captured_rid, "type": "done"}))

    push_task = asyncio.create_task(push_events())

    collected = []
    async for tag, payload in transport.send_streaming_request(
        page,
        url="http://test.com",
        headers={},
        body="{}",
        timeout_ms=3000,
    ):
        collected.append((tag, payload))

    await push_task

    assert ("status", 200) in collected
    assert ("chunk", b"Hello ") in collected
    assert ("chunk", b"CDP Binding!") in collected


@pytest.mark.asyncio
async def test_transport_streaming_timeout_raises():
    """Verify stream timeout before completion explicitly raises TimeoutError."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    binding_listener = None

    def mock_on_binding(name: str, callback):
        nonlocal binding_listener
        if name == "__aistudio_stream_push__":
            binding_listener = callback

    page.on_binding = MagicMock(side_effect=mock_on_binding)
    page.add_binding = AsyncMock()

    captured_rid = None

    async def fake_evaluate(expr, args=None, *a, **kw):
        nonlocal captured_rid
        if isinstance(args, dict) and "rid" in args:
            captured_rid = args["rid"]
        return

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    async def push_status_only():
        while captured_rid is None or binding_listener is None:
            await asyncio.sleep(0.01)
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "status", "status": 200})
        )

    push_task = asyncio.create_task(push_status_only())
    with pytest.raises(
        TimeoutError, match="streaming response timed out before completion"
    ):
        async for _ in transport.send_streaming_request(
            page,
            url="http://test.com",
            headers={},
            body="{}",
            timeout_ms=100,
        ):
            pass
    await push_task
