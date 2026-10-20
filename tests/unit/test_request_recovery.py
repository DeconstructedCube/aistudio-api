"""Regressions for cold capture and failures inside session recovery."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.api.state import RuntimeState
from aistudio_api.application import api_service_gemini as api
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext


def ready_context(marker: str) -> SessionContext:
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    page.evaluate = AsyncMock(return_value="fresh-snapshot")
    return SessionContext(
        page=page,
        snap_key="snapshot_fn",
        auth_user="0",
        auth_file=None,
        base_template={
            "url": "https://example.invalid/GenerateContent",
            "headers": {"X-Test-Context": marker},
            "body": '["models/gemini-3.7-flash",[],null,[],"old-snapshot"]',
        },
    )


@pytest.mark.parametrize("after_idle", [False, True])
async def test_first_capture_waits_for_ready_template(monkeypatch, after_idle):
    session = BrowserSession(port=0)
    service = RequestCaptureService(session)
    if after_idle:
        session.set_ready_context(ready_context("before-idle"))
        await service.capture(prompt="previous", model="models/gemini-4-argon")
        await session.close()

    context = ready_context("restored")

    async def restore_session(*args, **kwargs):
        session.set_ready_context(context)
        return context

    restore = AsyncMock(side_effect=restore_session)
    monkeypatch.setattr(session, "transition_to_account", restore)

    captured = await service.capture(prompt="hello", model="models/gemini-4-argon")

    assert captured is not None
    assert captured.headers == {"X-Test-Context": "restored"}
    body = json.loads(captured.body)
    assert body[0] == "models/gemini-4-argon"
    assert body[1] == [[[[None, "hello"]], "user"]]
    assert body[4] == "fresh-snapshot"
    restore.assert_awaited_once()


async def test_capture_during_transition_uses_new_context_template():
    session = BrowserSession(port=0)
    service = RequestCaptureService(session)
    session.set_ready_context(ready_context("old"))
    await service.capture(prompt="previous", model="models/gemini-4-argon")
    session._gate.set_preparing()

    task = asyncio.create_task(
        service.capture(prompt="next", model="models/gemini-4-argon")
    )
    try:
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        session.set_ready_context(ready_context("new"))
        captured = await task

    assert captured is not None
    assert captured.headers == {"X-Test-Context": "new"}


@pytest.fixture
def recovery_runtime(monkeypatch):
    state = MagicMock(spec=RuntimeState)
    state.account_service = None
    monkeypatch.setattr(api, "runtime_state", state)
    monkeypatch.setattr(api, "ensure_active_account", AsyncMock())
    record_event = MagicMock()
    monkeypatch.setattr(api, "record_rotator_event", record_event)
    switch = AsyncMock(
        side_effect=RuntimeError("BotGuard service captured but wire template missing")
    )
    monkeypatch.setattr(api, "try_switch_account", switch)
    client = MagicMock(spec=AIStudioClient)
    client._session = MagicMock(spec=BrowserSession)
    return state, switch, client, record_event


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("persistent_failure", [False, True])
async def test_request_retries_when_session_recovery_fails(
    recovery_runtime, stream, persistent_failure
):
    state, switch, client, record_event = recovery_runtime
    attempts = 0

    def fail_attempt():
        nonlocal attempts
        attempts += 1
        if persistent_failure or attempts == 1:
            raise RuntimeError("streaming request failed: timeout")

    async def generate(*args, **kwargs):
        fail_attempt()
        return ModelOutput(candidates=[Candidate(text="recovered")])

    async def generate_stream(*args, **kwargs):
        fail_attempt()
        yield ("body", "recovered")
        yield ("finish_reason", "STOP")

    client.generate_content = AsyncMock(side_effect=generate)
    client.stream_generate_content = generate_stream
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    if stream:
        response = await api.handle_gemini_generate_content(
            "gemini-4-argon", req, client, stream=True
        )
        assert isinstance(response, StreamingResponse)
        chunks = [
            chunk.decode() if isinstance(chunk, bytes) else chunk
            async for chunk in response.body_iterator
        ]
        if persistent_failure:
            assert len(chunks) == 1
            assert '"code": 500' in chunks[0]
            assert '"finishReason": "STOP"' not in chunks[0]
        else:
            assert any("recovered" in chunk for chunk in chunks)
            assert not any('"error"' in chunk for chunk in chunks)
    elif persistent_failure:
        with pytest.raises(HTTPException) as exc_info:
            await api.handle_gemini_generate_content(
                "gemini-4-argon", req, client, stream=False
            )
        assert exc_info.value.status_code == 500
    else:
        response = await api.handle_gemini_generate_content(
            "gemini-4-argon", req, client, stream=False
        )
        assert isinstance(response, GeminiGenerateContentResponse)
        assert response.candidates[0].content.parts[0].text == "recovered"

    assert attempts == (api.MAX_RETRIES if persistent_failure else 2)
    assert switch.await_count == (api.MAX_RETRIES - 1 if persistent_failure else 1)
    assert client._session._close_internal.await_count == switch.await_count
    if persistent_failure:
        state.record.assert_called_once_with("models/gemini-4-argon", "errors")
        record_event.assert_called_once_with("error", model="models/gemini-4-argon")
    else:
        assert state.record.call_count == 1
        assert state.record.call_args.args[:2] == ("models/gemini-4-argon", "success")


async def test_partial_stream_does_not_restart_session(recovery_runtime):
    state, switch, client, _record_event = recovery_runtime

    async def generate_stream(*args, **kwargs):
        yield ("body", "partial answer")
        raise RuntimeError("streaming request failed: timeout")

    client.stream_generate_content = generate_stream
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    response = await api.handle_gemini_generate_content(
        "gemini-4-argon", req, client, stream=True
    )
    assert isinstance(response, StreamingResponse)
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ]

    assert len(chunks) == 2
    assert "partial answer" in chunks[0]
    assert '"code": 500' in chunks[1]
    switch.assert_not_awaited()
    client._session._close_internal.assert_not_awaited()
    state.record.assert_called_once_with("models/gemini-4-argon", "errors")
