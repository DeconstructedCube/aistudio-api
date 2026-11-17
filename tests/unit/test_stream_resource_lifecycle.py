"""验证关闭公开 SSE 时真实客户端链路已等待 HTTP 响应释放。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import SessionContext
from aistudio_api.infrastructure.gateway.wire_spec import PartIndex


@pytest.mark.asyncio
async def test_sse_close_awaits_http_response_through_real_gateway(monkeypatch):
    closed = asyncio.Event()
    pending = asyncio.Event()
    part: list[object] = [None] * (PartIndex.TEXT + 1)
    part[PartIndex.TEXT] = "synthetic_partial"
    candidate = [[[part], "model"], 1]
    payload = (")]}'\n" + json.dumps([[[[candidate]]]])).encode("utf-8")

    class ObservedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload
            await pending.wait()

        async def aclose(self):
            # 断言必须等异步释放完成，不能只安排下一轮事件循环中的清理。
            await asyncio.sleep(0)
            closed.set()

    http_response = httpx.Response(200, stream=ObservedStream())

    async def send(_request: httpx.Request) -> httpx.Response:
        return http_response

    client = AIStudioClient()
    context = SessionContext(cookies={"SID": "synthetic_sid"})
    client.session.set_ready_context(context)
    monkeypatch.setattr(
        client,
        "prepare_request",
        AsyncMock(
            return_value=PreparedRequest(
                url="https://example.test/rpc", headers={}, body="[]", ctx=context
            )
        ),
    )
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}]}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(send), trust_env=False
    ) as http_client:
        monkeypatch.setattr(client.session._transport, "_client", http_client)
        response = gemini._build_gemini_streaming_response(
            client=client, req=req, model_path="gemini-3.8-flash"
        )
        iterator = response.body_iterator
        assert isinstance(iterator, AsyncGenerator)
        try:
            first = await iterator.__anext__()
            assert "synthetic_partial" in str(first)
            await iterator.aclose()
            assert closed.is_set()
            assert http_response.is_closed
        finally:
            # 失败基线也显式释放响应，避免测试自身依赖异步垃圾回收。
            await iterator.aclose()
            await http_response.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("keepalive", [False, True])
async def test_nonstream_client_cancellation_cleans_generation(monkeypatch, keepalive):
    client = MagicMock(spec=AIStudioClient)
    context = SessionContext(cookies={"SID": "synthetic_sid"})
    client.prepare_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/rpc", headers={}, body="[]", ctx=context
        )
    )
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}]}
    )
    tasks: list[asyncio.Task[object]] = []
    original_create_task = asyncio.create_task

    def create_task(coroutine):
        task = original_create_task(coroutine)
        tasks.append(task)
        return task

    async def wait_for(awaitable, timeout):
        interval = 0.01 if keepalive and timeout == 10.0 else timeout
        return await asyncio.wait_for(awaitable, timeout=interval)

    monkeypatch.setattr(
        gemini,
        "asyncio",
        SimpleNamespace(
            create_task=create_task,
            wait_for=wait_for,
            shield=asyncio.shield,
            CancelledError=asyncio.CancelledError,
            gather=asyncio.gather,
        ),
    )
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)
    monkeypatch.setattr(gemini.runtime_state, "rotator", None)

    started = asyncio.Event()
    closed = asyncio.Event()
    stay_pending = asyncio.Event()

    async def generate(**_kwargs):
        started.set()
        try:
            await stay_pending.wait()
        finally:
            closed.set()

    client.generate_content = AsyncMock(side_effect=generate)
    call = asyncio.create_task(
        gemini.handle_gemini_generate_content(
            "synthetic-model",
            req,
            client,
            stream=False,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=0.5)
        if keepalive:
            response = await asyncio.wait_for(call, timeout=0.5)
            assert isinstance(response, StreamingResponse)
            iterator = response.body_iterator
            assert isinstance(iterator, AsyncGenerator)
            assert await iterator.__anext__() == b"\n"
            await iterator.aclose()
        else:
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
        assert closed.is_set()
        assert all(task.done() for task in tasks)
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("phase", ["start", "body"])
@pytest.mark.parametrize("stop", ["send_error", "cancel"])
async def test_response_lifecycle_awaits_cleanup_before_return(
    monkeypatch, spec_version, phase, stop
):
    client = MagicMock(spec=AIStudioClient)
    context = SessionContext(cookies={"SID": "synthetic_sid"})
    client.prepare_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/rpc", headers={}, body="[]", ctx=context
        )
    )
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}]}
    )
    tasks: list[asyncio.Task[object]] = []
    original_create_task = asyncio.create_task

    def create_task(coroutine):
        task = original_create_task(coroutine)
        tasks.append(task)
        return task

    async def wait_for(awaitable, timeout):
        interval = 0.01 if phase == "start" and timeout == 10.0 else timeout
        return await asyncio.wait_for(awaitable, timeout=interval)

    monkeypatch.setattr(
        gemini,
        "asyncio",
        SimpleNamespace(
            create_task=create_task,
            wait_for=wait_for,
            shield=asyncio.shield,
            CancelledError=asyncio.CancelledError,
            gather=asyncio.gather,
        ),
    )
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)
    monkeypatch.setattr(gemini.runtime_state, "rotator", None)

    pending = asyncio.Event()
    closed = asyncio.Event()
    sending = asyncio.Event()

    async def generate(**_kwargs):
        try:
            await pending.wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    async def stream_content(**_kwargs):
        try:
            yield "body", "synthetic_partial"
            await pending.wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    client.generate_content = AsyncMock(side_effect=generate)
    client.stream_generate_content = MagicMock(side_effect=stream_content)
    response = await asyncio.wait_for(
        gemini.handle_gemini_generate_content(
            "synthetic-model", req, client, stream=phase == "body"
        ),
        timeout=0.5,
    )
    assert isinstance(response, StreamingResponse)
    scope = {
        "type": "http",
        "method": "POST",
        "asgi": {"version": "3.0", "spec_version": spec_version},
    }

    async def receive():
        await pending.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response." + phase:
            sending.set()
            if stop == "send_error":
                raise OSError("synthetic client disconnected")
            await pending.wait()

    call = asyncio.create_task(response(scope, receive, send))
    try:
        if stop == "send_error":
            with pytest.raises((ClientDisconnect, OSError)):
                await asyncio.wait_for(call, timeout=0.5)
        else:
            await asyncio.wait_for(sending.wait(), timeout=0.5)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
        assert sending.is_set()
        assert closed.is_set()
        assert all(task.done() for task in tasks)
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        iterator = response.body_iterator
        assert isinstance(iterator, AsyncGenerator)
        await iterator.aclose()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_stream_iterator_close_awaits_inner_generator_finally(monkeypatch):
    client = MagicMock(spec=AIStudioClient)
    context = SessionContext(cookies={"SID": "synthetic_sid"})
    client.prepare_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/rpc", headers={}, body="[]", ctx=context
        )
    )
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}]}
    )
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)

    closed = asyncio.Event()
    pending = asyncio.Event()

    async def stream_content(**_kwargs):
        try:
            yield "body", "synthetic_partial"
            await pending.wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    client.stream_generate_content = MagicMock(side_effect=stream_content)
    response = await gemini.handle_gemini_generate_content(
        "synthetic-model",
        req,
        client,
        stream=True,
    )
    assert isinstance(response, StreamingResponse)
    iterator = response.body_iterator
    assert isinstance(iterator, AsyncGenerator)
    await iterator.__anext__()
    await iterator.aclose()
    assert closed.is_set()
