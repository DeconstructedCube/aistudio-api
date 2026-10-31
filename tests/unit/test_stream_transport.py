"""Unit tests for HTTPStreamTransport."""

from __future__ import annotations

import httpx
import pytest

from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.gateway.transport import HTTPStreamTransport


@pytest.mark.asyncio
async def test_transport_non_streaming_request():
    """Verify non-streaming HTTP POST request."""
    transport = HTTPStreamTransport()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'["response data"]')

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._client = mock_client

    status, body = await transport.send_hooked_request(
        url="https://example.com/api",
        headers={"Content-Type": "application/json"},
        body="{}",
    )

    assert status == 200
    assert body == b'["response data"]'


@pytest.mark.asyncio
async def test_transport_streaming_request():
    """Verify streaming HTTP POST chunks."""
    transport = HTTPStreamTransport()

    def handler(request: httpx.Request) -> httpx.Response:
        async def stream():
            yield b"chunk1"
            yield b"chunk2"

        return httpx.Response(200, content=stream())

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._client = mock_client

    events = []
    async for tag, payload in transport.send_streaming_request(
        url="https://example.com/stream",
        headers={},
        body="{}",
    ):
        events.append((tag, payload))

    assert ("status", 200) in events
    assert ("chunk", b"chunk1") in events
    assert ("chunk", b"chunk2") in events


@pytest.mark.asyncio
async def test_transport_error_handling():
    """Verify non-streaming HTTP failure raises BrowserTransportError."""
    transport = HTTPStreamTransport()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._client = mock_client

    with pytest.raises(BrowserTransportError, match="HTTP request failed"):
        await transport.send_hooked_request(
            url="https://example.com/fail",
            headers={},
            body="{}",
        )
