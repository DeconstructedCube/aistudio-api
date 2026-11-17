"""验证 Decisions 与 CountTokens 的预算、断开隔离及请求账号隔离。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from aistudio_api.api.app import app
from aistudio_api.api.dependencies import get_client
from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import (
    GeminiCountTokensRequest,
    GeminiCreateDecisionRequest,
    GeminiGenerateContentRequest,
)
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.application.account_orchestrator import record_rotator_event
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    AuthError,
    SessionExpiredError,
    TransportError,
    UsageLimitExceeded,
)
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import GatewaySession, SessionContext


def _decision_request() -> GeminiCreateDecisionRequest:
    return GeminiCreateDecisionRequest.model_validate(
        {
            "input": "A cracked cup.",
            "questions": {
                "damaged": {"type": "noul", "instructions": "Is it damaged?"}
            },
        }
    )


def _count_request() -> GeminiCountTokensRequest:
    return GeminiCountTokensRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "Count these tokens."}]}]}
    )


def _decision_success() -> dict[str, object]:
    return {
        "answers": {"damaged": {"type": "noul", "noul": 0.9}},
        "usage": {"total_input_tokens": 3, "total_tokens": 3},
    }


@pytest.fixture
def ready_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    # 只替代账号就绪及上游请求边界，使用真实业务处理器与响应模型。
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini, "try_switch_account", AsyncMock(return_value=True))
    monkeypatch.setattr(gemini, "record_rotator_event", MagicMock())
    monkeypatch.setattr(gemini.runtime_state, "record", MagicMock())
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)
    monkeypatch.setattr(gemini.runtime_state, "rotator", None)
    client = MagicMock(spec=AIStudioClient)
    client.session = MagicMock(spec=GatewaySession)
    client.session.active_context = SessionContext(cookies={"SID": "synthetic_sid"})
    client.prepare_decision_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/decision",
            headers={},
            body="[]",
            ctx=client.session.active_context,
        )
    )
    client.create_decision = AsyncMock(return_value=_decision_success())
    client.count_tokens = AsyncMock(return_value={"totalTokens": 3})
    return client


def _account_service() -> tuple[MagicMock, AccountMeta, AccountMeta, list[AccountMeta]]:
    accounts = {
        name: AccountMeta(
            id=name,
            name=name,
            email=None,
            created_at="2026-11-15",
            cookie_id="cookie-" + name,
        )
        for name in ("account-a", "account-b")
    }
    first = accounts["account-a"]
    second = accounts["account-b"]
    active = [first]
    service = MagicMock(spec=AccountService)
    service.get_active_account.side_effect = lambda: active[0]
    service.get_account.side_effect = accounts.get
    service.get_account_auth_path.return_value = None
    service.is_cookie_generation_current.side_effect = lambda account_id, cookie_id: (
        accounts[account_id].cookie_id == cookie_id
    )
    service.rotate_account_cookies = AsyncMock(return_value={"ok": False})
    return service, first, second, active


def _context(tmp_path: Path, account: AccountMeta) -> SessionContext:
    return SessionContext(
        auth_file=str(tmp_path / account.id / "auth.json"),
        auth_user=account.auth_user,
        tier=account.tier,
        cookie_id=account.cookie_id or "",
        cookies={"SID": "synthetic_sid"},
    )


@pytest.mark.asyncio
async def test_decision_403_does_not_consume_ordinary_retry_budget(
    ready_client: MagicMock,
) -> None:
    ready_client.create_decision.side_effect = [
        AuthError("permission denied"),
        AuthError("permission denied"),
        *[
            TransportError("temporary connection failure")
            for _ in range(gemini.MAX_RETRIES - 1)
        ],
        _decision_success(),
    ]

    response = await gemini.handle_gemini_decisions(_decision_request(), ready_client)

    assert response.answers["damaged"]["noul"] == 0.9
    assert ready_client.create_decision.await_count == gemini.MAX_RETRIES + 2
    assert (
        cast(AsyncMock, gemini.try_switch_account).await_count == gemini.MAX_RETRIES - 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [UsageLimitExceeded("short rate limit"), SessionExpiredError("expired")],
)
async def test_decision_ordinary_failures_stop_at_ordinary_budget(
    ready_client: MagicMock, error: Exception
) -> None:
    ready_client.create_decision.side_effect = error

    with pytest.raises(HTTPException) as raised:
        await gemini.handle_gemini_decisions(_decision_request(), ready_client)

    assert ready_client.create_decision.await_count == gemini.MAX_RETRIES
    assert raised.value.status_code == gemini.classify_gemini_error_payload(error)[0]


@pytest.mark.asyncio
async def test_decision_interleaved_retry_exhaustion_preserves_transport_error(
    ready_client: MagicMock,
) -> None:
    ready_client.create_decision.side_effect = [
        error
        for _ in range(gemini.MAX_RETRIES)
        for error in (
            AuthError("permission denied"),
            TransportError("final connection failure"),
        )
    ]

    with pytest.raises(HTTPException) as raised:
        await gemini.handle_gemini_decisions(_decision_request(), ready_client)

    assert raised.value.status_code == 502
    assert ready_client.create_decision.await_count == 2 * gemini.MAX_RETRIES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [SessionExpiredError("expired"), UsageLimitExceeded("short rate limit")]
)
async def test_count_tokens_retry_exhaustion_preserves_last_failure(
    ready_client: MagicMock, failure: Exception
) -> None:
    ready_client.count_tokens.side_effect = failure

    with pytest.raises(HTTPException) as raised:
        await _invoke("count_tokens", ready_client)

    assert ready_client.count_tokens.await_count == gemini.MAX_RETRIES
    assert raised.value.status_code == gemini.classify_gemini_error_payload(failure)[0]


@pytest.mark.asyncio
async def test_decision_403_staircase_remains_bounded(ready_client: MagicMock) -> None:
    ready_client.create_decision.side_effect = AuthError("permission denied")

    with pytest.raises(HTTPException) as raised:
        await gemini.handle_gemini_decisions(_decision_request(), ready_client)

    assert raised.value.status_code == 403
    assert ready_client.create_decision.await_count == gemini.AUTH_403_MAX_RETRIES + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["account", "enabled", "cookie_id", "auth_user", "tier"]
)
async def test_decision_replay_revalidates_cached_account_context(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed: str,
) -> None:
    service, first, second, active = _account_service()
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    original = PreparedRequest(
        url="https://example.test/decision",
        headers={},
        body="old-signed-body",
        ctx=_context(tmp_path, first),
    )
    refreshed: list[PreparedRequest] = []

    async def prepare(**_kwargs: object) -> PreparedRequest:
        if not refreshed:
            prepared = original
        else:
            prepared = PreparedRequest(
                url=original.url,
                headers={},
                body="new-signed-body",
                ctx=_context(tmp_path, active[0]),
            )
        refreshed.append(prepared)
        return prepared

    calls = 0

    async def create(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            if changed == "account":
                active[0] = second
            elif changed == "enabled":
                first.enabled = False
            elif changed == "cookie_id":
                first.cookie_id = "new-cookie-generation"
            elif changed == "auth_user":
                first.auth_user = "2"
            else:
                first.tier = "Pro"
            raise AuthError("permission denied")
        assert kwargs["prepared"] is not original
        return _decision_success()

    ready_client.prepare_decision_request.side_effect = prepare
    ready_client.create_decision.side_effect = create

    response = await gemini.handle_gemini_decisions(_decision_request(), ready_client)

    assert response.answers["damaged"]["noul"] == 0.9
    assert ready_client.prepare_decision_request.await_count == 2
    assert ready_client.create_decision.await_count == 2


@pytest.mark.asyncio
async def test_decision_current_cached_request_is_replayed(
    ready_client: MagicMock,
) -> None:
    ready_client.create_decision.side_effect = [
        AuthError("permission denied"),
        _decision_success(),
    ]

    response = await gemini.handle_gemini_decisions(_decision_request(), ready_client)

    assert response.answers["damaged"]["noul"] == 0.9
    assert ready_client.prepare_decision_request.await_count == 1
    first, second = ready_client.create_decision.await_args_list
    assert first.kwargs["prepared"] is second.kwargs["prepared"]


async def _invoke(kind: str, client: AIStudioClient) -> object:
    if kind == "decisions":
        return await gemini.handle_gemini_decisions(_decision_request(), client)
    return await gemini.handle_gemini_count_tokens(
        "gemini-3.8-flash", _count_request(), client
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        AuthError("permission denied"),
        SessionExpiredError("expired"),
        TransportError("connection failure"),
        UsageLimitExceeded("short rate limit"),
    ],
)
async def test_count_tokens_error_uses_pre_request_account_snapshot(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: Exception,
) -> None:
    service, first, second, active = _account_service()
    rotator = MagicMock()
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", rotator)
    monkeypatch.setattr(gemini, "try_switch_account", AsyncMock(return_value=False))
    monkeypatch.setattr(gemini, "record_rotator_event", record_rotator_event)
    ready_client.session.active_context = _context(tmp_path, first)

    async def request(**_kwargs: object) -> dict[str, object]:
        active[0] = second
        ready_client.session.active_context = _context(tmp_path, second)
        raise failure

    ready_client.count_tokens.side_effect = request

    with pytest.raises(HTTPException):
        await _invoke("count_tokens", ready_client)

    switch_args = cast(AsyncMock, gemini.try_switch_account).await_args
    if isinstance(failure, SessionExpiredError):
        assert switch_args is not None
        assert switch_args.kwargs["failed_account_id"] == first.id
        assert switch_args.kwargs["expected_cookie_id"] == first.cookie_id
    elif isinstance(failure, AuthError):
        rotator.record_auth_error.assert_called_once_with(
            first.id, model="models/gemini-3.8-flash"
        )
    else:
        assert switch_args is not None
        assert switch_args.kwargs["failed_account_id"] == first.id
        if isinstance(failure, UsageLimitExceeded):
            rotator.record_rate_limited.assert_called_once_with(
                first.id, model="models/gemini-3.8-flash"
            )
        else:
            rotator.record_error.assert_called_once_with(
                first.id, model="models/gemini-3.8-flash"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["unknown", "deleted", "stale"])
@pytest.mark.parametrize(
    "failure",
    [TransportError("connection failure"), UsageLimitExceeded("short rate limit")],
)
async def test_count_tokens_unknown_deleted_or_stale_source_never_penalizes_new_active(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
    failure: Exception,
) -> None:
    service, first, second, active = _account_service()
    rotator = MagicMock()
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", rotator)
    monkeypatch.setattr(gemini, "try_switch_account", AsyncMock(return_value=False))
    monkeypatch.setattr(gemini, "record_rotator_event", record_rotator_event)
    ready_client.session.active_context = (
        SessionContext(cookies={"SID": "synthetic_sid"})
        if source == "unknown"
        else _context(tmp_path, first)
    )

    async def request(**_kwargs: object) -> dict[str, object]:
        active[0] = second
        ready_client.session.active_context = _context(tmp_path, second)
        if source == "deleted":
            service.get_account.side_effect = lambda account_id: (
                second if account_id == second.id else None
            )
        elif source == "stale":
            first.cookie_id = "new-cookie-generation"
        raise failure

    ready_client.count_tokens.side_effect = request

    with pytest.raises(HTTPException):
        await _invoke("count_tokens", ready_client)

    rotator.record_rate_limited.assert_not_called()
    rotator.record_error.assert_not_called()
    if source == "unknown":
        switch_args = cast(AsyncMock, gemini.try_switch_account).await_args
        assert switch_args is not None
        assert switch_args.kwargs["failed_account_id"] is None


@pytest.mark.asyncio
async def test_count_tokens_old_401_does_not_disable_reimported_generation(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, first, _second, _active = _account_service()
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini, "try_switch_account", AsyncMock(return_value=False))
    ready_client.session.active_context = _context(tmp_path, first)
    calls = 0

    async def request(**_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            first.cookie_id = "new-cookie-generation"
            ready_client.session.active_context = _context(tmp_path, first)
            raise SessionExpiredError("old generation expired")
        return {"totalTokens": 3}

    ready_client.count_tokens.side_effect = request

    response = await gemini.handle_gemini_count_tokens(
        "gemini-3.8-flash", _count_request(), ready_client
    )

    assert response.totalTokens == 3
    assert calls == 2
    cast(AsyncMock, gemini.try_switch_account).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "unsupported",
    [
        {"systemInstruction": {"parts": [{"text": "System prompt."}]}},
        {"tools": [{"functionDeclarations": [{"name": "lookup"}]}]},
    ],
)
async def test_count_tokens_rejects_unsupported_fields_in_both_inputs(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    nested: bool,
    unsupported: dict[str, object],
) -> None:
    monkeypatch.setattr(settings, "api_keys", frozenset())
    content = {
        "contents": [{"parts": [{"text": "Count these tokens."}]}],
        **unsupported,
    }
    payload = {"generateContentRequest": content} if nested else content
    app.dependency_overrides[get_client] = lambda: ready_client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/v1beta/models/gemini-3.8-flash:countTokens", json=payload
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    assert "contents only" in response.json()["error"]["message"]
    ready_client.count_tokens.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_count_tokens_empty_optional_fields_keep_contents_contract(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    nested: bool,
) -> None:
    monkeypatch.setattr(settings, "api_keys", frozenset())
    content = {
        "contents": [{"parts": [{"text": "Count these tokens."}]}],
        "systemInstruction": {"parts": []},
        "tools": [],
    }
    payload = {"generateContentRequest": content} if nested else content
    app.dependency_overrides[get_client] = lambda: ready_client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/v1beta/models/gemini-3.8-flash:countTokens", json=payload
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["totalTokens"] == 3
    ready_client.count_tokens.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["decisions", "generate", "stream"])
async def test_completed_account_a_never_clears_account_b_penalties(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    monkeypatch.setattr(settings, "persist_rotator", False)
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    first = store.save_account(
        name="虚构账户 A",
        email=None,
        storage_state={"cookies": []},
        cookie_id="synthetic-cookie-a",
    )
    second = store.save_account(
        name="虚构账户 B",
        email=None,
        storage_state={"cookies": []},
        cookie_id="synthetic-cookie-b",
    )
    active = [first]
    service = MagicMock(spec=AccountService)
    service.get_account.side_effect = store.get_account
    service.get_active_account.side_effect = lambda: active[0]
    service.is_cookie_generation_current.side_effect = lambda account_id, cookie_id: (
        (account := store.get_account(account_id)) is not None
        and account.cookie_id == cookie_id
    )
    rotator = AccountRotator(store)
    model = (
        "models/gemini-decision-1" if kind == "decisions" else "models/gemini-3.8-flash"
    )
    rotator.record_auth_error(second.id, model=model)
    rotator.record_auth_error(second.id, model=model)
    rotator.record_rate_limited(second.id, model=model)
    before = rotator.get_account_stats(second.id)
    assert before is not None
    assert before.auth_errors == 2
    assert before.get_cooldown_remaining(model) > 0
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", rotator)
    monkeypatch.setattr(gemini, "record_rotator_event", record_rotator_event)
    prepared = PreparedRequest(
        url="https://example.test/rpc",
        headers={},
        body="[]",
        ctx=_context(tmp_path / "accounts", first),
    )
    ready_client.prepare_decision_request.return_value = prepared
    ready_client.prepare_request = AsyncMock(return_value=prepared)

    async def create(**_kwargs: object) -> dict[str, object]:
        active[0] = second
        return _decision_success()

    async def generate(**_kwargs: object) -> ModelOutput:
        active[0] = second
        return ModelOutput(candidates=[Candidate(text="synthetic_ok", finish_reason=1)])

    async def stream(**_kwargs: object) -> AsyncGenerator[tuple[str, object], None]:
        active[0] = second
        yield "body", "synthetic_ok"
        yield "finish_reason", 1

    ready_client.create_decision.side_effect = create
    ready_client.generate_content = AsyncMock(side_effect=generate)
    ready_client.stream_generate_content = MagicMock(side_effect=stream)
    if kind == "decisions":
        response = await gemini.handle_gemini_decisions(
            _decision_request(), ready_client
        )
        assert response.answers["damaged"]["noul"] == 0.9
    else:
        request = GeminiGenerateContentRequest.model_validate(
            {"contents": [{"parts": [{"text": "Synthetic input."}]}]}
        )
        response = await gemini.handle_gemini_generate_content(
            "gemini-3.8-flash", request, ready_client, stream=kind == "stream"
        )
        if kind == "stream":
            assert isinstance(response, StreamingResponse)
            chunks = [chunk async for chunk in response.body_iterator]
            assert any("synthetic_ok" in str(chunk) for chunk in chunks)
        else:
            assert isinstance(response, GeminiGenerateContentResponse)
            assert response.candidates[0].content.parts[0].text == "synthetic_ok"

    after = rotator.get_account_stats(second.id)
    succeeded = rotator.get_account_stats(first.id)
    assert after is not None
    assert succeeded is not None
    assert after.auth_errors == 2
    assert after.get_cooldown_remaining(model) > 0
    assert rotator.is_account_available(second.id, model=model) is False
    assert succeeded.total_success == 1


def test_record_event_without_explicit_id_keeps_active_account_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _first, second, active = _account_service()
    active[0] = second
    rotator = MagicMock()
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", rotator)

    record_rotator_event("error", model="synthetic-model")

    rotator.record_error.assert_called_once_with(second.id, model="synthetic-model")


@pytest.mark.parametrize("account_id", ["account-a", "unknown-account", ""])
def test_record_event_explicit_id_never_falls_back_to_active_account(
    monkeypatch: pytest.MonkeyPatch,
    account_id: str,
) -> None:
    service, first, second, active = _account_service()
    active[0] = second
    rotator = MagicMock()
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", rotator)

    record_rotator_event("error", model="synthetic-model", account_id=account_id)

    if account_id == first.id:
        rotator.record_error.assert_called_once_with(first.id, model="synthetic-model")
    else:
        rotator.record_error.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["decisions", "generate", "stream"])
@pytest.mark.parametrize(
    "failure",
    [
        AuthError("signer permission denied"),
        SessionExpiredError("signer session expired"),
        TransportError("signer connection failure"),
        UsageLimitExceeded("signer short rate limit"),
    ],
)
async def test_preparation_failure_keeps_original_account_snapshot(
    ready_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    failure: Exception,
) -> None:
    """签名过程中切到 B 后，A 的签名失败仍归到 A；B 只承接后续成功请求。"""
    service, first, second, active = _account_service()
    rotator = MagicMock()
    switch = AsyncMock(return_value=False)
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", rotator)
    monkeypatch.setattr(gemini, "try_switch_account", switch)
    monkeypatch.setattr(gemini, "record_rotator_event", record_rotator_event)
    ready_client.session.active_context = _context(tmp_path, first)
    prepares = 0

    async def prepare(**_kwargs: object) -> PreparedRequest:
        nonlocal prepares
        prepares += 1
        if prepares == 1:
            await asyncio.sleep(0)
            active[0] = second
            ready_client.session.active_context = _context(tmp_path, second)
            raise failure
        return PreparedRequest(
            url="https://example.test/rpc",
            headers={},
            body="[]",
            ctx=ready_client.session.active_context,
        )

    async def stream(**_kwargs: object) -> AsyncGenerator[tuple[str, object], None]:
        yield "body", "synthetic_ok"
        yield "finish_reason", 1

    ready_client.prepare_decision_request.side_effect = prepare
    ready_client.prepare_request = AsyncMock(side_effect=prepare)
    ready_client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[Candidate(text="synthetic_ok", finish_reason=1)]
        )
    )
    ready_client.stream_generate_content = MagicMock(side_effect=stream)
    model = (
        "models/gemini-decision-1" if kind == "decisions" else "models/gemini-3.8-flash"
    )

    async def invoke() -> object:
        if kind == "decisions":
            return await gemini.handle_gemini_decisions(
                _decision_request(), ready_client
            )
        request = GeminiGenerateContentRequest.model_validate(
            {"contents": [{"parts": [{"text": "Synthetic input."}]}]}
        )
        result = await gemini.handle_gemini_generate_content(
            "gemini-3.8-flash", request, ready_client, stream=kind == "stream"
        )
        if isinstance(result, StreamingResponse):
            return [chunk async for chunk in result.body_iterator]
        return result

    if isinstance(failure, AuthError) and not isinstance(failure, SessionExpiredError):
        await invoke()
        assert prepares == 2
        rotator.record_auth_error.assert_called_once_with(first.id, model=model)
        rotator.record_success.assert_called_once()
        assert rotator.record_success.call_args.args == (second.id,)
        assert rotator.record_success.call_args.kwargs["model"] == model
        if kind == "decisions":
            # Decisions 自带输入用量，与生成接口的空用量分别核对。
            assert (
                rotator.record_success.call_args.kwargs["usage"]["prompt_tokens"] == 3
            )
        switch.assert_not_awaited()
    else:
        if kind == "stream":
            chunks = await invoke()
            assert "error" in str(chunks)
        else:
            with pytest.raises(HTTPException):
                await invoke()
        assert prepares == 1
        assert switch.await_args is not None
        assert switch.await_args.kwargs["failed_account_id"] == first.id
        if isinstance(failure, SessionExpiredError):
            assert switch.await_args.kwargs["expected_cookie_id"] == first.cookie_id
        elif isinstance(failure, UsageLimitExceeded):
            rotator.record_rate_limited.assert_called_once_with(first.id, model=model)
        else:
            rotator.record_error.assert_called_once_with(first.id, model=model)
        rotator.record_success.assert_not_called()
