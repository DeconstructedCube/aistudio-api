"""验证整请求截止覆盖所有阶段，并清理非流式后台任务。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect

from aistudio_api import config
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.domain.errors import AuthError, TransportError
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import SessionContext


@pytest_asyncio.fixture
async def deadline_runtime(monkeypatch):
    client = MagicMock(spec=AIStudioClient)
    context = SessionContext(cookies={"SID": "synthetic_sid"})
    client.prepare_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/rpc",
            headers={},
            body="[]",
            ctx=context,
        )
    )
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}],
        }
    )
    state = SimpleNamespace(
        client=client,
        req=req,
        tasks=[],
        keepalive=False,
        switch=AsyncMock(return_value=True),
        events=MagicMock(),
        record=MagicMock(),
    )

    def create_task(coroutine):
        task = asyncio.create_task(coroutine)
        state.tasks.append(task)
        return task

    async def wait_for(awaitable, timeout):
        # 只缩短保活间隔，真实请求截止时间仍由独立预算管理。
        interval = 0.01 if state.keepalive and timeout == 10.0 else timeout
        return await asyncio.wait_for(awaitable, timeout=interval)

    monkeypatch.setattr(
        gemini,
        "asyncio",
        SimpleNamespace(
            create_task=create_task,
            wait_for=wait_for,
            shield=asyncio.shield,
            CancelledError=asyncio.CancelledError,
            timeout_at=asyncio.timeout_at,
            get_running_loop=asyncio.get_running_loop,
            gather=asyncio.gather,
        ),
    )
    monkeypatch.setattr(gemini.settings, "request_timeout_seconds", 0.08)
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini, "try_switch_account", state.switch)
    monkeypatch.setattr(gemini, "record_rotator_event", state.events)
    monkeypatch.setattr(gemini.runtime_state, "record", state.record)
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)
    monkeypatch.setattr(gemini.runtime_state, "rotator", None)
    yield state
    for task in state.tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*state.tasks, return_exceptions=True)


async def _payloads(state, *, stream) -> list[dict[str, object]]:
    try:
        result = await gemini.handle_gemini_generate_content(
            "synthetic-model",
            state.req,
            state.client,
            stream=stream,
        )
    except HTTPException as exc:
        code, message, status = gemini.classify_gemini_error_payload(exc)
        return [{"error": {"code": code, "message": message, "status": status}}]
    assert isinstance(result, StreamingResponse)
    chunks = [chunk async for chunk in result.body_iterator]
    text = "".join(
        chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8")
        for chunk in chunks
    )
    if not stream:
        return [json.loads(text)]
    return [
        json.loads(event.removeprefix("data: "))
        for event in text.split("\n\n")
        if event
    ]


def _last_error(payloads: list[dict[str, object]]) -> dict[str, object]:
    error = payloads[-1].get("error")
    assert isinstance(error, dict)
    return error


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "phase", ["activate", "prepare", "generate", "auth", "switch", "retry"]
)
async def test_total_deadline_covers_all_request_stages(
    deadline_runtime, monkeypatch, stream, phase
):
    state = deadline_runtime
    stay_pending = asyncio.Event()
    interrupted = asyncio.Event()

    async def block(*_args, **_kwargs):
        try:
            await stay_pending.wait()
        finally:
            interrupted.set()

    async def generate(**_kwargs):
        if phase == "retry":
            await asyncio.sleep(0.03)
            raise TransportError("synthetic read timeout")
        if phase == "auth":
            raise AuthError("synthetic forbidden")
        if phase == "switch":
            raise TransportError("synthetic read timeout")
        await block()

    async def stream_content(**kwargs):
        await generate(**kwargs)
        yield "body", "unreachable"

    state.client.generate_content = AsyncMock(side_effect=generate)
    state.client.stream_generate_content = MagicMock(side_effect=stream_content)
    if phase == "activate":
        monkeypatch.setattr(gemini, "ensure_active_account", block)
    elif phase == "prepare":
        state.client.prepare_request.side_effect = block
    elif phase == "switch":
        state.switch.side_effect = block
    elif phase == "auth":
        account = AccountMeta(
            id="acc_a",
            name="虚构账户",
            email=None,
            created_at="synthetic",
            cookie_id="cookie_a",
            tier="Pro",
        )
        state.client.prepare_request.return_value.ctx = SessionContext(
            auth_file="virtual/acc_a/auth.json",
            auth_user="0",
            tier="Pro",
            cookie_id="cookie_a",
            cookies={"SID": "synthetic_sid"},
        )
        service = SimpleNamespace(
            get_active_account=lambda: account,
            get_account=lambda _id: account,
            is_cookie_generation_current=lambda _id, _cookie_id: True,
            rotate_account_cookies=AsyncMock(side_effect=block),
        )
        monkeypatch.setattr(gemini.runtime_state, "account_service", service)

    payloads = await asyncio.wait_for(_payloads(state, stream=stream), timeout=0.5)

    assert _last_error(payloads)["code"] == 504
    assert _last_error(payloads)["status"] == "DEADLINE_EXCEEDED"
    assert "usageMetadata" not in payloads[-1]
    assert all(task.done() for task in state.tasks)
    state.record.assert_not_called()
    state.events.assert_not_called()
    if phase == "retry":
        calls = (
            state.client.stream_generate_content.call_count
            if stream
            else state.client.generate_content.await_count
        )
        assert calls < gemini.MAX_RETRIES
    else:
        assert interrupted.is_set()


@pytest.mark.asyncio
async def test_stream_deadline_keeps_partial_output_without_replay(deadline_runtime):
    state = deadline_runtime
    closed = asyncio.Event()
    stay_pending = asyncio.Event()

    async def stream_content(**_kwargs):
        try:
            yield "body", "synthetic_partial"
            await stay_pending.wait()
        finally:
            closed.set()

    state.client.stream_generate_content = MagicMock(side_effect=stream_content)
    payloads = await asyncio.wait_for(_payloads(state, stream=True), timeout=0.5)
    candidates = payloads[0].get("candidates")
    assert isinstance(candidates, list)
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    content = candidate.get("content")
    assert isinstance(content, dict)
    assert content["parts"] == [{"text": "synthetic_partial"}]
    assert _last_error(payloads)["status"] == "DEADLINE_EXCEEDED"
    assert state.client.stream_generate_content.call_count == 1
    state.switch.assert_not_awaited()
    state.events.assert_not_called()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_nonstream_keepalive_returns_deadline_json_and_cleans_task(
    deadline_runtime,
):
    state = deadline_runtime
    state.keepalive = True
    stay_pending = asyncio.Event()
    closed = asyncio.Event()

    async def generate(**_kwargs):
        try:
            await stay_pending.wait()
        finally:
            closed.set()

    state.client.generate_content = AsyncMock(side_effect=generate)
    payloads = await asyncio.wait_for(_payloads(state, stream=False), timeout=0.5)
    assert _last_error(payloads)["code"] == 504
    assert _last_error(payloads)["status"] == "DEADLINE_EXCEEDED"
    assert all(task.done() for task in state.tasks)
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("keepalive", [False, True])
async def test_nonstream_client_cancellation_cleans_generation(
    deadline_runtime, keepalive
):
    state = deadline_runtime
    state.keepalive = keepalive
    started = asyncio.Event()
    closed = asyncio.Event()
    stay_pending = asyncio.Event()

    async def generate(**_kwargs):
        started.set()
        try:
            await stay_pending.wait()
        finally:
            closed.set()

    state.client.generate_content = AsyncMock(side_effect=generate)
    call = asyncio.create_task(
        gemini.handle_gemini_generate_content(
            "synthetic-model",
            state.req,
            state.client,
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
        assert all(task.done() for task in state.tasks)
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)


@pytest.mark.parametrize(
    "value,expected", [("0.2", 0.2), ("900", 900.0), ("1200", 1200.0)]
)
def test_request_deadline_config_accepts_positive_finite_seconds(
    monkeypatch, value, expected
):
    monkeypatch.setenv("AISTUDIO_REQUEST_TIMEOUT_SECONDS", value)
    assert (
        config._load_positive_float_env(
            "AISTUDIO_REQUEST_TIMEOUT_SECONDS", default=900.0
        )
        == expected
    )


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "invalid"])
def test_request_deadline_config_rejects_invalid_budget(monkeypatch, value):
    monkeypatch.setenv("AISTUDIO_REQUEST_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="positive finite number"):
        config._load_positive_float_env(
            "AISTUDIO_REQUEST_TIMEOUT_SECONDS", default=900.0
        )


def test_request_deadline_config_defaults_to_slow_model_window(monkeypatch):
    monkeypatch.delenv("AISTUDIO_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert (
        config._load_positive_float_env(
            "AISTUDIO_REQUEST_TIMEOUT_SECONDS", default=900.0
        )
        == 900.0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("phase", ["start", "body"])
@pytest.mark.parametrize("stop", ["send_error", "cancel"])
async def test_response_lifecycle_awaits_cleanup_before_return(
    deadline_runtime, monkeypatch, spec_version, phase, stop
):
    state = deadline_runtime
    state.keepalive = phase == "start"
    monkeypatch.setattr(gemini.settings, "request_timeout_seconds", 1.0)
    pending = asyncio.Event()
    closed = asyncio.Event()
    sending = asyncio.Event()

    async def generate(**_kwargs):
        try:
            await pending.wait()
        finally:
            # 必须等待资源清理结束，不能只触发 cancel 后就返回。
            await asyncio.sleep(0)
            closed.set()

    async def stream_content(**_kwargs):
        try:
            yield "body", "synthetic_partial"
            await pending.wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    state.client.generate_content = AsyncMock(side_effect=generate)
    state.client.stream_generate_content = MagicMock(side_effect=stream_content)
    response = await asyncio.wait_for(
        gemini.handle_gemini_generate_content(
            "synthetic-model", state.req, state.client, stream=phase == "body"
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
        assert all(task.done() for task in state.tasks)
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        # 失败基线也显式收尾，不依赖异步生成器的垃圾回收。
        iterator = response.body_iterator
        assert isinstance(iterator, AsyncGenerator)
        await iterator.aclose()


@pytest.mark.asyncio
async def test_stream_iterator_close_awaits_inner_generator_finally(deadline_runtime):
    state = deadline_runtime
    closed = asyncio.Event()
    pending = asyncio.Event()

    async def stream_content(**_kwargs):
        try:
            yield "body", "synthetic_partial"
            await pending.wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    state.client.stream_generate_content = MagicMock(side_effect=stream_content)
    response = await gemini.handle_gemini_generate_content(
        "synthetic-model",
        state.req,
        state.client,
        stream=True,
    )
    assert isinstance(response, StreamingResponse)
    iterator = response.body_iterator
    assert isinstance(iterator, AsyncGenerator)
    await iterator.__anext__()
    await iterator.aclose()
    assert closed.is_set()
