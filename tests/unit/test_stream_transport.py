"""Unit tests for HTTPStreamTransport."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import httpx
import pytest

from aistudio_api.domain.errors import TransportError
from aistudio_api.infrastructure.gateway.client import PreparedRequest
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

    status, body = await transport.send_request(
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
    """Verify non-streaming HTTP failure raises TransportError."""
    transport = HTTPStreamTransport()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._client = mock_client

    with pytest.raises(TransportError, match="HTTP request failed"):
        await transport.send_request(
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
    prepared = PreparedRequest(url="https://example.com", headers={}, body="{}")
    results: list[str] = []
    async for event_type, val in gateway.stream_chat(
        request=prepared, model="models/gemini-2.5-flash"
    ):
        if event_type == "body" and isinstance(val, str):
            results.append(val)

    joined = "".join(results)
    assert (
        joined
        == "嗨！我是你的酒馆工程搭档。平时在这套工作区里，一起搞的事情挺多的：🎉🚀"
    )
    assert "\ufffd" not in joined


@pytest.mark.asyncio
async def test_transport_sanitizes_lone_utf16_surrogates():
    """Verify lone UTF-16 surrogates in request body never raise UnicodeEncodeError."""
    transport = HTTPStreamTransport()
    captured: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        return httpx.Response(200, content=b"ok")

    transport._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    status, _ = await transport.send_request(
        url="https://example.com/api",
        headers={},
        body='{"text":"broken \ud83d emoji"}',
    )
    assert status == 200
    assert len(captured) == 1
    assert captured[0] == b'{"text":"broken ? emoji"}'


@pytest.mark.asyncio
async def test_http2_ping_keepalive_emits_ping_frame_on_idle_read_slice(monkeypatch):
    """Verify HTTP/2 read slicing emits RFC 9113 PING frames under _write_lock without aborting long requests."""
    import asyncio

    import httpcore
    from httpcore._async import http2 as hc_http2

    from aistudio_api.infrastructure.gateway import transport as transport_mod

    transport_mod._install_http2_ping_keepalive()
    monkeypatch.setattr(transport_mod, "H2_PING_INTERVAL_SECONDS", 0.01)

    read_calls = 0
    written_payloads: list[bytes] = []
    ping_opaques: list[bytes] = []

    class FakeStream:
        async def read(self, _max_bytes: int, _timeout: float | None = None) -> bytes:
            nonlocal read_calls
            read_calls += 1
            if read_calls <= 2:
                raise httpcore.ReadTimeout("idle slice")
            return b"h2_frame_bytes"

        async def write(self, data: bytes, _timeout: float | None = None) -> None:
            written_payloads.append(data)

    class FakeH2State:
        def ping(self, opaque: bytes) -> None:
            ping_opaques.append(opaque)

        def data_to_send(self) -> bytes:
            return b"PING_FRAME"

        def receive_data(self, data: bytes) -> list[str]:
            return [f"decoded:{data.decode()}"]

    class DummyConn:
        READ_NUM_BYTES = 65536
        _read_exception = None
        _write_exception = None
        _connection_terminated = None
        _connection_error = False
        _network_stream = FakeStream()
        _h2_state = FakeH2State()
        _write_lock = asyncio.Lock()

    dummy = DummyConn()
    req = httpcore.Request(
        "POST",
        "https://alkalimakersuite-pa.clients6.google.com/rpc",
        extensions={"timeout": {"read": 5.0, "write": 5.0}},
    )
    events = await hc_http2.AsyncHTTP2Connection._read_incoming_data(dummy, req)  # type: ignore[arg-type]
    assert events == ["decoded:h2_frame_bytes"]
    assert len(ping_opaques) == 2
    assert len(ping_opaques[0]) == 8
    assert ping_opaques[0] != ping_opaques[1]
    assert written_payloads == [b"PING_FRAME", b"PING_FRAME"]


@pytest.mark.asyncio
async def test_reset_stale_client_defers_close_while_concurrent_request_inflight():
    """Verify _reset_stale_client detaches the pool immediately but defers aclose() until in-flight requests drain."""
    from unittest.mock import AsyncMock, MagicMock

    transport = HTTPStreamTransport()
    client = MagicMock(spec=httpx.AsyncClient)
    client._transport = MagicMock(spec=httpx.AsyncHTTPTransport)
    client.is_closed = False
    client.aclose = AsyncMock()
    transport._client = client

    # Two concurrent requests acquire the same pooled client
    transport._acquire_inflight(client)
    transport._acquire_inflight(client)

    # Request 1 hits a connection error and triggers _reset_stale_client
    await transport._reset_stale_client(client)
    assert transport._client is None
    client.aclose.assert_not_awaited()

    # Request 1 finishes its finally block; Request 2 is still streaming
    await transport._release_inflight(client)
    client.aclose.assert_not_awaited()

    # Request 2 finishes; stale client is now closed
    await transport._release_inflight(client)
    client.aclose.assert_awaited_once()
