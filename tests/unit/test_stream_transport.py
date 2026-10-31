"""Unit tests for HTTPStreamTransport."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import httpx
import pytest

from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
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


@pytest.mark.asyncio
async def test_streaming_gateway_split_utf8_chunks():
    """Verify that multi-byte UTF-8 characters split across byte chunks are not corrupted into replacement characters."""

    class MockSession:
        async def send_streaming_request(
            self, **kwargs: object
        ) -> AsyncGenerator[tuple[str, object], None]:
            yield ("status", 200)
            text_content = (
                "嗨！我是你的酒馆工程搭档。平时在这套工作区里，一起搞的事情挺多的：🎉🚀"
            )
            chunk_data = [
                [[[[[None, text_content]], "model"]]],
                None,
                [5, None, 19],
                None,
                None,
                None,
                None,
                "resp_123",
            ]
            full_raw = (")]}'\n[[ " + json.dumps(chunk_data) + " ]]").encode("utf-8")
            # Intentionally split into 3-byte chunks to cut Chinese characters and 4-byte emojis mid-character
            for i in range(0, len(full_raw), 3):
                yield ("chunk", full_raw[i : i + 3])

    gateway = StreamingGateway(session=MockSession())  # type: ignore[arg-type]
    captured = CapturedRequest(url="https://example.com", headers={}, body="{}")
    results: list[str] = []
    async for event_type, val in gateway.stream_chat(
        captured=captured, model="models/gemini-2.5-flash"
    ):
        if event_type == "body" and isinstance(val, str):
            results.append(val)

    joined = "".join(results)
    assert (
        joined
        == "嗨！我是你的酒馆工程搭档。平时在这套工作区里，一起搞的事情挺多的：🎉🚀"
    )
    assert "\ufffd" not in joined
