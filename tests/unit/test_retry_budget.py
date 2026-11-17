"""验证普通失败与 403 重试预算独立，并保留最终错误。"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.domain.errors import (
    AuthError,
    SessionExpiredError,
    StaleSessionError,
    TransportError,
)
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import SessionContext


@pytest.fixture
def request_runtime(monkeypatch):
    client = MagicMock(spec=AIStudioClient)
    client.prepare_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/rpc",
            headers={},
            body="[]",
            model="models/synthetic-model",
            ctx=SessionContext(cookies={"SID": "synthetic_sid"}),
        )
    )
    switch = AsyncMock(return_value=True)
    record = MagicMock()
    events = MagicMock()
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini, "try_switch_account", switch)
    monkeypatch.setattr(gemini, "record_rotator_event", events)
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)
    monkeypatch.setattr(gemini.runtime_state, "rotator", None)
    monkeypatch.setattr(gemini.runtime_state, "record", record)
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}]}
    )
    return SimpleNamespace(
        client=client, req=req, switch=switch, record=record, events=events
    )


def _set_outcomes(client, outcomes: Sequence[Exception | ModelOutput]) -> None:
    pending = deque(outcomes)

    def next_outcome():
        # 保留最后一个失败，额外重试仍产生同类错误，便于发现超预算请求。
        outcome = pending.popleft() if len(pending) > 1 else pending[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def generate_content(**_kwargs):
        return next_outcome()

    async def stream_content(**_kwargs):
        output = next_outcome()
        yield "body", output.text
        yield "finish_reason", 1
        yield "usage", output.usage

    client.generate_content = AsyncMock(side_effect=generate_content)
    client.stream_generate_content = MagicMock(side_effect=stream_content)


async def _invoke(request_runtime, stream: bool):
    result = await gemini.handle_gemini_generate_content(
        "synthetic-model", request_runtime.req, request_runtime.client, stream=stream
    )
    if not stream:
        assert isinstance(result, GeminiGenerateContentResponse)
        return result
    assert isinstance(result, StreamingResponse)
    chunks = [chunk async for chunk in result.body_iterator]
    text = "".join(
        chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8")
        for chunk in chunks
    )
    return [
        json.loads(event.removeprefix("data: "))
        for event in text.split("\n\n")
        if event
    ]


async def _error(request_runtime, stream: bool) -> dict[str, object]:
    if stream:
        payloads = await _invoke(request_runtime, stream=True)
        assert isinstance(payloads, list)
        assert "usageMetadata" not in payloads[-1]
        return payloads[-1]["error"]
    with pytest.raises(HTTPException) as raised:
        await _invoke(request_runtime, stream=False)
    code, message, status = gemini.classify_gemini_error_payload(raised.value)
    return {"code": code, "message": message, "status": status}


def _generation_calls(client, stream: bool) -> int:
    return (
        client.stream_generate_content.call_count
        if stream
        else client.generate_content.await_count
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("phase", ["prepare", "generate"])
async def test_transport_failures_do_not_use_auth_retry_budget(
    request_runtime, stream, phase
):
    failure = TransportError("synthetic ReadTimeout")
    _set_outcomes(request_runtime.client, [failure])
    if phase == "prepare":
        request_runtime.client.prepare_request.side_effect = failure

    error = await _error(request_runtime, stream)

    assert error == {"code": 502, "message": str(failure), "status": "UNAVAILABLE"}
    assert request_runtime.client.prepare_request.await_count == gemini.MAX_RETRIES
    assert _generation_calls(request_runtime.client, stream) == (
        gemini.MAX_RETRIES if phase == "generate" else 0
    )
    assert request_runtime.switch.await_count == gemini.MAX_RETRIES - 1
    request_runtime.record.assert_called_once_with("models/synthetic-model", "errors")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_auth_replays_do_not_consume_transport_budget(request_runtime, stream):
    output = ModelOutput(candidates=[Candidate(text="synthetic_ok", finish_reason=1)])
    _set_outcomes(
        request_runtime.client,
        [AuthError("synthetic forbidden")] * 2
        + [TransportError("synthetic ReadTimeout")] * (gemini.MAX_RETRIES - 1)
        + [output],
    )

    result = await _invoke(request_runtime, stream)

    if stream:
        assert isinstance(result, list)
        assert any("synthetic_ok" in json.dumps(payload) for payload in result)
        assert all("error" not in payload for payload in result)
    else:
        assert isinstance(result, GeminiGenerateContentResponse)
        assert result.candidates[0].content.parts[0].text == "synthetic_ok"
    assert _generation_calls(request_runtime.client, stream) == gemini.MAX_RETRIES + 2
    assert request_runtime.switch.await_count == gemini.MAX_RETRIES - 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_interleaved_auth_and_transport_exhaustion_keeps_transport_error(
    request_runtime, stream
):
    failure = TransportError("synthetic final ReadTimeout")
    _set_outcomes(
        request_runtime.client,
        [
            error
            for _ in range(gemini.MAX_RETRIES)
            for error in (AuthError("synthetic forbidden"), failure)
        ],
    )

    error = await _error(request_runtime, stream)

    assert error == {"code": 502, "message": str(failure), "status": "UNAVAILABLE"}
    assert _generation_calls(request_runtime.client, stream) == 2 * gemini.MAX_RETRIES
    # 第五次 403 切号一次，加上前四次传输失败的切号。
    assert request_runtime.switch.await_count == gemini.MAX_RETRIES


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_auth_only_exhaustion_keeps_permission_denied(request_runtime, stream):
    failure = AuthError("synthetic forbidden")
    _set_outcomes(request_runtime.client, [failure])

    error = await _error(request_runtime, stream)

    assert error == {
        "code": 403,
        "message": str(failure),
        "status": "PERMISSION_DENIED",
    }
    assert (
        _generation_calls(request_runtime.client, stream)
        == gemini.AUTH_403_MAX_RETRIES + 1
    )
    assert request_runtime.switch.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        SessionExpiredError("synthetic expired"),
        StaleSessionError("synthetic stale credentials"),
    ],
)
async def test_other_retry_exhaustion_keeps_last_error(
    request_runtime, stream, failure
):
    _set_outcomes(request_runtime.client, [failure])

    error = await _error(request_runtime, stream)

    code, message, status = gemini.classify_gemini_error_payload(failure)
    assert error == {"code": code, "message": message, "status": status}
    assert code != 429
    assert _generation_calls(request_runtime.client, stream) == gemini.MAX_RETRIES


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["body", "thinking"])
async def test_streamed_content_prevents_transport_replay(request_runtime, event_type):
    async def partial_stream(**_kwargs):
        yield event_type, "synthetic_partial"
        raise TransportError("synthetic interrupted stream")

    request_runtime.client.stream_generate_content = MagicMock(
        side_effect=partial_stream
    )

    payloads = await _invoke(request_runtime, stream=True)
    assert isinstance(payloads, list)

    assert (
        payloads[0]["candidates"][0]["content"]["parts"][0]["text"]
        == "synthetic_partial"
    )
    assert payloads[-1]["error"]["code"] == 502
    assert request_runtime.client.stream_generate_content.call_count == 1
    request_runtime.switch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("change", ["disable", "cookie", "auth_user", "tier"])
async def test_auth_replay_uses_current_account_binding(
    request_runtime, monkeypatch, tmp_path, stream, change
):
    account_a = AccountMeta(
        id="acc_a",
        name="虚构账户 A",
        email=None,
        created_at="synthetic",
        cookie_id="cookie_a",
        tier="Pro",
    )
    account_b = AccountMeta(
        id="acc_b",
        name="虚构账户 B",
        email=None,
        created_at="synthetic",
        cookie_id="cookie_b",
        tier="Pro",
    )
    by_id = {account.id: account for account in (account_a, account_b)}
    state = SimpleNamespace(active=account_a)
    service = SimpleNamespace(
        get_active_account=lambda: state.active,
        get_account=by_id.get,
        is_cookie_generation_current=lambda account_id, cookie_id: (
            by_id[account_id].cookie_id == cookie_id
        ),
    )
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    prepared_contexts = []
    sent_contexts = []

    async def prepare(**_kwargs):
        account = state.active
        ctx = SessionContext(
            auth_file=str(tmp_path / account.id / "auth.json"),
            auth_user=account.auth_user,
            tier=account.tier,
            cookie_id=account.cookie_id,
            cookies={"SID": "synthetic_sid"},
        )
        prepared_contexts.append(ctx)
        return PreparedRequest(
            url="https://example.test/rpc", headers={}, body="[]", ctx=ctx
        )

    def next_result(prepared):
        sent_contexts.append(prepared.ctx)
        if len(sent_contexts) == 1:
            if change == "disable":
                account_a.enabled = False
                state.active = account_b
            elif change == "cookie":
                account_a.cookie_id = "cookie_new"
            elif change == "auth_user":
                account_a.auth_user = "1"
            else:
                account_a.tier = "Free"
            raise AuthError("synthetic forbidden")
        return ModelOutput(candidates=[Candidate(text="synthetic_ok", finish_reason=1)])

    async def generate(*, prepared, **_kwargs):
        return next_result(prepared)

    async def stream_content(*, prepared, **_kwargs):
        output = next_result(prepared)
        yield "body", output.text

    request_runtime.client.prepare_request = AsyncMock(side_effect=prepare)
    request_runtime.client.generate_content = AsyncMock(side_effect=generate)
    request_runtime.client.stream_generate_content = MagicMock(
        side_effect=stream_content
    )

    await _invoke(request_runtime, stream)

    assert len(prepared_contexts) == 2
    assert sent_contexts[-1] is prepared_contexts[-1]
    assert sent_contexts[-1].auth_file == str(tmp_path / state.active.id / "auth.json")
    assert sent_contexts[-1].cookie_id == state.active.cookie_id
    assert sent_contexts[-1].auth_user == state.active.auth_user
    assert sent_contexts[-1].tier == state.active.tier


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events,expected_signatures",
    [
        (
            [
                ("thought_signature", "independent"),
                (
                    "tool_calls",
                    [
                        {
                            "name": "lookup",
                            "args": {},
                            "id": "call_a",
                            "thought_signature": "call_signature",
                        },
                        {
                            "name": "lookup",
                            "args": {},
                            "id": "call_b",
                            "thought_signature": "call_signature_b",
                        },
                    ],
                ),
            ],
            ["independent", "call_signature", "call_signature_b"],
        ),
        (
            [("thought_signature", "first"), ("thought_signature", "second")],
            ["first", "second"],
        ),
        (
            [
                ("thought_signature", "same"),
                (
                    "tool_calls",
                    [
                        {
                            "name": "lookup",
                            "args": {},
                            "id": "call_a",
                            "thought_signature": "same",
                        },
                    ],
                ),
            ],
            ["same"],
        ),
        (
            [
                ("thought_signature", "pending"),
                ("unknown", "ignored"),
                ("body", "synthetic_ok"),
            ],
            ["pending"],
        ),
    ],
)
async def test_stream_preserves_independent_and_call_signatures(
    request_runtime, events, expected_signatures
):
    async def stream_content(**_kwargs):
        for event in events:
            yield event
        yield "done", None

    request_runtime.client.stream_generate_content = MagicMock(
        side_effect=stream_content
    )
    payloads = await _invoke(request_runtime, stream=True)
    assert isinstance(payloads, list)
    parts = [
        part
        for payload in payloads
        for candidate in payload.get("candidates", [])
        for part in candidate["content"]["parts"]
    ]
    assert [
        part["thoughtSignature"] for part in parts if part.get("thoughtSignature")
    ] == expected_signatures
    call_ids = [
        part["functionCall"]["id"] for part in parts if part.get("functionCall")
    ]
    assert call_ids == [
        call["id"] for kind, calls in events if kind == "tool_calls" for call in calls
    ]
    assert not any("error" in payload for payload in payloads)
    assert "usageMetadata" in payloads[-1]


@pytest.mark.asyncio
async def test_emitted_signature_prevents_transport_replay(request_runtime):
    async def stream_content(**_kwargs):
        yield "thought_signature", "first"
        yield "thought_signature", "second"
        raise TransportError("synthetic interrupted stream")

    request_runtime.client.stream_generate_content = MagicMock(
        side_effect=stream_content
    )
    payloads = await _invoke(request_runtime, stream=True)
    assert isinstance(payloads, list)
    assert payloads[0]["candidates"][0]["content"]["parts"] == [
        {"thoughtSignature": "first"}
    ]
    assert payloads[-1]["error"]["code"] == 502
    assert request_runtime.client.stream_generate_content.call_count == 1
    request_runtime.switch.assert_not_awaited()
