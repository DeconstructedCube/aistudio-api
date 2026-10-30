"""Unit tests for XHRStreamTransport and CDP native stream binding."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    STREAM_CLEANUP_JS,
    STREAMING_INIT_JS,
)
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport


def test_streaming_scripts_defined_and_non_empty():
    """Verify streaming initialization and cleanup scripts are properly loaded."""
    assert len(STREAMING_INIT_JS.strip()) > 0
    assert len(STREAM_CLEANUP_JS.strip()) > 0
    assert "__AISTUDIO__" in STREAMING_INIT_JS
    assert "__AISTUDIO__" in STREAM_CLEANUP_JS


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
    ):
        collected.append((tag, payload))

    await push_task

    assert ("status", 200) in collected
    assert ("chunk", b"Hello ") in collected
    assert ("chunk", b"CDP Binding!") in collected


@pytest.mark.asyncio
async def test_transport_streaming_page_closed_raises():
    """Verify stream immediately raises BrowserTransportError if browser page closes mid-stream."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(side_effect=[False, True, True, True])
    page.on_binding = MagicMock()
    page.add_binding = AsyncMock()
    page.evaluate = AsyncMock()

    with pytest.raises(
        BrowserTransportError, match="Browser page closed during streaming"
    ):
        async for _ in transport.send_streaming_request(
            page,
            url="http://test.com",
            headers={},
            body="{}",
        ):
            pass


@pytest.mark.asyncio
async def test_transport_abort_stream_by_rid():
    """Verify abort_stream targets specific active stream by rid without disturbing other streams."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    page.on_binding = MagicMock()
    page.add_binding = AsyncMock()

    captured_rids: list[str] = []

    async def fake_evaluate(expr, args=None, *a, **kw):
        if isinstance(args, dict) and "rid" in args:
            captured_rids.append(args["rid"])
        return

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    assert transport.in_flight_count == 0

    async def run_stream():
        async for _ in transport.send_streaming_request(
            page,
            url="http://test.com",
            headers={},
            body="{}",
        ):
            pass

    task1 = asyncio.create_task(run_stream())
    task2 = asyncio.create_task(run_stream())
    await asyncio.sleep(0.05)
    assert transport.in_flight_count == 2
    assert len(captured_rids) == 2

    # Abort unknown rid returns False
    assert transport.abort_stream("non_existent_rid") is False
    assert transport.in_flight_count == 2

    # Abort stream 1 specifically
    rid1 = captured_rids[0]
    assert transport.abort_stream(rid1, "client_disconnected") is True

    with pytest.raises(
        RuntimeError, match="streaming request failed: client_disconnected"
    ):
        await task1

    assert transport.in_flight_count == 1

    # Close transport interrupts remaining stream 2
    transport.close("transport_closed")
    with pytest.raises(
        RuntimeError, match="streaming request failed: transport_closed"
    ):
        await task2

    assert transport.in_flight_count == 0


@pytest.mark.asyncio
async def test_replay_failure_logs_and_attaches_cdp_net_evidence():
    """Verify in-page fetch failure attaches CDP Network.loadingFailed evidence to BrowserTransportError."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    page.has_net_trace = False
    page.rpc_net_requests = {}
    page.rpc_net_failures = []
    page._cached_cookies = {"SAPISID": "test_sapisid"}

    req_cb = None
    fail_cb = None

    def capture_req(cb):
        nonlocal req_cb
        req_cb = cb
        return lambda: None

    def capture_fail(cb):
        nonlocal fail_cb
        fail_cb = cb
        return lambda: None

    page.on_request = MagicMock(side_effect=capture_req)
    page.on_response = MagicMock(return_value=lambda: None)
    page.on_loading_failed = MagicMock(side_effect=capture_fail)

    async def fake_eval(_expr, _args=None, **_kwargs):
        if req_cb and fail_cb:
            req_cb(
                {
                    "request_id": "req_1",
                    "url": "https://alkalimakersuite-pa.clients6.google.com/$rpc/MakerSuiteService/GenerateContent",
                }
            )
            fail_cb(
                {
                    "requestId": "req_1",
                    "errorText": "net::ERR_CONNECTION_RESET",
                    "canceled": False,
                }
            )
        return {"status": 0, "body": "network error"}

    page.evaluate = AsyncMock(side_effect=fake_eval)

    with pytest.raises(
        BrowserTransportError, match="replay failed: network error"
    ) as exc_info:
        await transport.send_hooked_request(
            page,
            url="https://alkalimakersuite-pa.clients6.google.com/$rpc/MakerSuiteService/GenerateContent",
            headers={},
            body="[]",
        )
    assert exc_info.value.net_evidence is not None
    assert len(exc_info.value.net_evidence) == 1
    assert exc_info.value.net_evidence[0]["error_text"] == "net::ERR_CONNECTION_RESET"


@pytest.mark.asyncio
async def test_streaming_gateway_raises_on_empty_200_stream_and_retains_trailing_usage():
    """Verify StreamingGateway rejects 200 stream with 0 chunks and retains trailing usage chunks after finish_reason."""
    mock_session = MagicMock(spec=BrowserSession)
    captured = CapturedRequest(
        url="https://alkalimakersuite-pa.clients6.google.com/$rpc/GenerateContent",
        headers={},
        body='["models/gemini-3.7-flash"]',
        model="models/gemini-3.7-flash",
    )
    gateway = StreamingGateway(session=mock_session)

    # 1. Empty 200 stream raises BrowserTransportError
    async def empty_stream(**_kwargs):
        yield ("status", 200)

    mock_session.send_streaming_request = MagicMock(side_effect=empty_stream)
    with pytest.raises(
        BrowserTransportError, match="stream ended before any data arrived"
    ):
        async for _ in gateway.stream_chat(
            captured=captured, model="models/gemini-3.7-flash"
        ):
            pass

    # 2. Trailing usage chunk after finish_reason=1 (and intermediate finish_reason=22) is preserved
    async def multi_chunk_stream(**_kwargs):
        yield ("status", 200)
        payload = json.dumps(
            [
                [
                    [
                        [[[[[None, "Part 1 "]], "model"], 22]],
                    ],
                    [
                        [[[[[None, "Part 2"]], "model"], 1]],
                    ],
                    [
                        None,
                        None,
                        [15, 25, 40, 10, None, None, None, None, None, 5],
                        None,
                        None,
                        None,
                        None,
                        "resp_final_99",
                    ],
                ]
            ]
        ).encode("utf-8")
        yield ("chunk", payload)

    mock_session.send_streaming_request = MagicMock(side_effect=multi_chunk_stream)
    events = [
        ev
        async for ev in gateway.stream_chat(
            captured=captured, model="models/gemini-3.7-flash"
        )
    ]
    assert ("body", "Part 1 ") in events
    assert ("body", "Part 2") in events
    assert ("finish_reason", 1) in events
    assert ("response_id", "resp_final_99") in events
    usage_events = [payload for kind, payload in events if kind == "usage"]
    assert len(usage_events) == 1
    assert isinstance(usage_events[0], dict)
    assert usage_events[0]["prompt_tokens"] == 15
    assert usage_events[0]["cached_tokens"] == 10
