"""验证账号身份验证拦截与服务受限诊断的自动禁用和故障转移。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.domain.errors import (
    AccountRestrictedError,
    AccountVerificationRequiredError,
    classify_error,
)
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import GatewaySession

VERIFY_ERROR_BODY = (
    '[,[7,"Your account must be verified before it can continue using the Gemini API. '
    "Verify your account at https://aistudio.google.com/verify-account. "
    'Learn more at https://ai.google.dev/gemini-api/docs/verification#verify-account.",[]]]'
)


def test_classify_error_detects_verification_required_and_restricted() -> None:
    err_verify = classify_error(403, VERIFY_ERROR_BODY)
    assert isinstance(err_verify, AccountVerificationRequiredError)
    assert "verify-account" in str(err_verify)

    err_restricted = classify_error(
        403,
        '[,[7,"https://myaccount.google.com/restrictions/200 servicerestricted"]]',
    )
    assert isinstance(err_restricted, AccountRestrictedError)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "failure_factory",
    [
        lambda: classify_error(403, VERIFY_ERROR_BODY),
        lambda: AccountRestrictedError("AI Studio 账号服务受限 (servicerestricted)"),
    ],
    ids=["verification-required", "service-restricted"],
)
async def test_verification_and_restriction_auto_disable_and_failover(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
    failure_factory,
) -> None:
    from aistudio_api.application import account_orchestrator

    store = AccountStore(accounts_dir=tmp_path / "accounts")
    service = AccountService(store)
    acc1 = service.save_account_from_cookies(
        name="Verify Acc",
        email="verify@example.test",
        storage_state={"cookies": [{"name": "SID", "value": "sid1"}]},
        cookie_id="cid_1",
    )
    acc2 = service.save_account_from_cookies(
        name="Healthy Acc",
        email="healthy@example.test",
        storage_state={"cookies": [{"name": "SID", "value": "sid2"}]},
        cookie_id="cid_2",
    )
    session = GatewaySession()
    await service.activate_account(acc1.id, session)
    rotator = AccountRotator(store)

    client = MagicMock(spec=AIStudioClient)
    client.session = session

    async def prepare(*, ctx=None, **_kwargs):
        active_ctx = ctx or session.active_context
        return PreparedRequest(
            url="https://example.test/rpc",
            headers={},
            body="[]",
            model="models/gemini-3.8-flash",
            ctx=active_ctx,
        )

    client.prepare_request = AsyncMock(side_effect=prepare)

    calls = 0

    def next_outcome():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure_factory()
        return ModelOutput(candidates=[Candidate(text="healthy_ok", finish_reason=1)])

    async def generate_content(**_kwargs):
        return next_outcome()

    async def stream_generate_content(**_kwargs):
        out = next_outcome()
        yield ("body", out.text)
        yield ("finish_reason", 1)

    client.generate_content = AsyncMock(side_effect=generate_content)
    client.stream_generate_content = MagicMock(side_effect=stream_generate_content)

    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", rotator)
    monkeypatch.setattr(gemini.runtime_state, "client", client)
    monkeypatch.setattr(account_orchestrator, "_last_switch_time", 0.0)

    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
    )
    result = await gemini.handle_gemini_generate_content(
        "gemini-3.8-flash", req, client, stream=stream
    )

    if stream:
        assert isinstance(result, StreamingResponse)
        chunks = [
            c if isinstance(c, str) else bytes(c).decode("utf-8")
            async for c in result.body_iterator
        ]
        joined = "".join(chunks)
        assert "healthy_ok" in joined
        assert '"error"' not in joined
    else:
        assert isinstance(result, GeminiGenerateContentResponse)
        assert result.candidates[0].content.parts[0].text == "healthy_ok"

    # acc1 must be permanently disabled without wasting 4 replay/resign retries
    assert calls == 2
    reloaded_1 = service.get_account(acc1.id)
    reloaded_2 = service.get_account(acc2.id)
    assert reloaded_1 is not None and reloaded_1.enabled is False
    assert reloaded_2 is not None and reloaded_2.enabled is True
    active = service.get_active_account()
    assert active is not None and active.id == acc2.id


@pytest.mark.asyncio
async def test_stale_verification_error_does_not_disable_reimported_account(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts_stale")
    service = AccountService(store)
    acc = service.save_account_from_cookies(
        name="Reimported",
        email="user@example.test",
        storage_state={"cookies": [{"name": "SID", "value": "new_sid"}]},
        cookie_id="cookie_new",
    )
    from aistudio_api.infrastructure.gateway.session import SessionContext

    stale_ctx = SessionContext(
        auth_file=str(service.get_account_auth_path(acc.id)),
        auth_user="0",
        cookie_id="cookie_old",
        cookies={"SID": "old_sid"},
    )
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", AccountRotator(store))

    should_retry = await gemini.handle_attempt_exception(
        classify_error(403, VERIFY_ERROR_BODY),
        attempt=0,
        model_path="gemini-3.8-flash",
        normalized_model="models/gemini-3.8-flash",
        request_ctx=stale_ctx,
        has_yielded_data=False,
    )
    assert should_retry is True
    reloaded = service.get_account(acc.id)
    assert reloaded is not None and reloaded.enabled is True


@pytest.mark.asyncio
async def test_verification_required_no_backup_raises_403(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.application import account_orchestrator

    store = AccountStore(accounts_dir=tmp_path / "accounts_single")
    service = AccountService(store)
    acc = service.save_account_from_cookies(
        name="Single Verify",
        email="single@example.test",
        storage_state={"cookies": [{"name": "SID", "value": "sid1"}]},
        cookie_id="cid_1",
    )
    session = GatewaySession()
    await service.activate_account(acc.id, session)
    client = MagicMock(spec=AIStudioClient)
    client.session = session
    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "rotator", AccountRotator(store))
    monkeypatch.setattr(gemini.runtime_state, "client", client)
    monkeypatch.setattr(account_orchestrator, "_last_switch_time", 0.0)

    req_ctx = session.active_context
    with pytest.raises(HTTPException) as exc_info:
        await gemini.handle_attempt_exception(
            classify_error(403, VERIFY_ERROR_BODY),
            attempt=0,
            model_path="gemini-3.8-flash",
            normalized_model="models/gemini-3.8-flash",
            request_ctx=req_ctx,
            has_yielded_data=False,
        )
    assert exc_info.value.status_code == 403
    reloaded = service.get_account(acc.id)
    assert reloaded is not None and reloaded.enabled is False
