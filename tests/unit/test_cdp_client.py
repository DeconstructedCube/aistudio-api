"""Unit tests for native async CDP client and protocol handling."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import (
    BLOCKED_URL_PATTERNS,
    CDPConnection,
    CDPError,
    CDPPage,
)


def test_blocked_url_patterns_coverage():
    """Verify kernel-level asset pruning covers images, fonts, and trackers."""
    assert "*.png" in BLOCKED_URL_PATTERNS
    assert "*.jpg" in BLOCKED_URL_PATTERNS
    assert "*.woff2" in BLOCKED_URL_PATTERNS
    assert "*google-analytics.com*" in BLOCKED_URL_PATTERNS
    assert "*play.google.com/log*" in BLOCKED_URL_PATTERNS


@pytest.mark.asyncio
async def test_cdp_connection_send_and_receive():
    """Test CDPConnection request-response matching and error handling."""
    conn = CDPConnection("ws://127.0.0.1:9222/devtools/page/test")
    fake_ws = AsyncMock()
    conn.ws = fake_ws

    # Mock sending and receiving
    async def fake_send(text):
        data = json.loads(text)
        req_id = data["id"]
        # Simulate response in future
        fut = conn._futures.get(req_id)
        if fut and not fut.done():
            fut.set_result({"result": {"value": 42}})

    fake_ws.send = fake_send

    res = await conn.send("Runtime.evaluate", {"expression": "1 + 1"})
    assert res == {"result": {"value": 42}}


@pytest.mark.asyncio
async def test_cdp_connection_error_handling():
    """Test CDPConnection properly raises CDPError on JSON-RPC error."""
    conn = CDPConnection("ws://127.0.0.1:9222/devtools/page/test")
    fake_ws = AsyncMock()
    conn.ws = fake_ws

    async def fake_send(text):
        data = json.loads(text)
        req_id = data["id"]
        fut = conn._futures.get(req_id)
        if fut and not fut.done():
            fut.set_exception(CDPError(code=-32000, message="Target closed"))

    fake_ws.send = fake_send

    with pytest.raises(CDPError) as exc_info:
        await conn.send("Page.navigate", {"url": "https://example.com"})
    assert "Target closed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_cdp_page_blocked_urls_configuration():
    """Test CDPPage sets blocked URLs on Network domain."""
    conn = MagicMock(spec=CDPConnection)
    conn.send = AsyncMock(return_value={})
    conn.on = MagicMock()

    page = CDPPage(conn, target_id="target-1")
    await page.set_blocked_urls(BLOCKED_URL_PATTERNS)

    conn.send.assert_called_with(
        "Network.setBlockedURLs", {"urls": BLOCKED_URL_PATTERNS}
    )


@pytest.mark.asyncio
async def test_cdp_page_evaluate_argument_wrapping():
    """Test CDPPage.evaluate correctly wraps expressions with arguments and unwraps value."""
    conn = MagicMock(spec=CDPConnection)
    conn.send = AsyncMock(
        return_value={"result": {"type": "string", "value": "hello world"}}
    )

    page = CDPPage(conn, target_id="target-1")
    res = await page.evaluate("(args) => args.val", {"val": "hello world"})

    assert res == "hello world"
    call_args = conn.send.call_args[0]
    assert call_args[0] == "Runtime.evaluate"
    expr = call_args[1]["expression"]
    assert "hello world" in expr
    assert call_args[1]["awaitPromise"] is True


@pytest.mark.asyncio
async def test_cdp_page_evaluate_trailing_semicolon_handling():
    """Test CDPPage.evaluate strips trailing semicolons before wrapping into expression."""
    conn = MagicMock(spec=CDPConnection)
    conn.send = AsyncMock(return_value={"result": {"type": "string", "value": "ok"}})
    page = CDPPage(conn, target_id="target-1")

    # 1. Function with trailing semicolon and arguments
    await page.evaluate("(args) => { return args.val; };\n", {"val": "test"})
    call_args = conn.send.call_args[0]
    expr1 = call_args[1]["expression"]
    assert ";)" not in expr1
    assert expr1.startswith("((args) => { return args.val; })(")

    # 2. Arrow function with trailing semicolon and without arguments
    await page.evaluate("() => { return 42; };;;", None)
    call_args2 = conn.send.call_args[0]
    expr2 = call_args2[1]["expression"]
    assert ";)" not in expr2
    assert expr2 == "(() => { return 42; })()"


@pytest.mark.asyncio
async def test_cdp_page_set_cookies_normalization():
    """Test cookie normalization handles __Host- prefixes and domains properly."""
    conn = MagicMock(spec=CDPConnection)
    conn.send = AsyncMock(return_value={})

    page = CDPPage(conn, target_id="target-1")
    cookies = [
        {
            "name": "SID",
            "value": "sid123",
            "domain": ".google.com",
            "path": "/",
            "sameSite": "None",
        },
        {
            "name": "__Host-GAPS",
            "value": "gaps123",
            "domain": "accounts.google.com",
            "path": "/",
        },
    ]

    await page.set_cookies(cookies)

    conn.send.assert_called()
    payload = conn.send.call_args[0][1]["cookies"]
    assert len(payload) == 2

    sid = next(c for c in payload if c["name"] == "SID")
    assert sid["domain"] == ".google.com"
    assert sid["sameSite"] == "None"

    host_cookie = next(c for c in payload if c["name"] == "__Host-GAPS")
    # Must NOT have domain attribute with leading dot, must have url
    assert "domain" not in host_cookie
    assert host_cookie["url"] == "https://accounts.google.com/"


@pytest.mark.asyncio
async def test_cdp_page_is_alive():
    """Test CDPPage.is_alive fast liveness probe."""
    conn = MagicMock(spec=CDPConnection)
    conn._closed = False
    conn.send = AsyncMock(return_value={"result": {"type": "number", "value": 1}})

    page = CDPPage(conn, target_id="target-1")
    assert await page.is_alive(timeout_s=0.5) is True

    # When evaluate throws (CDP disconnected)
    conn.send.side_effect = RuntimeError("CDP closed")
    assert await page.is_alive(timeout_s=0.5) is False
