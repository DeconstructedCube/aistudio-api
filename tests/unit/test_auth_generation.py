"""验证旧请求与旧探活不能隔离重新导入后的凭据。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from aistudio_api.api import app as app_module
from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.application.chat_service import NormalizedGeminiRequest
from aistudio_api.domain.errors import SessionExpiredError
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.client import PreparedRequest
from aistudio_api.infrastructure.gateway.session import SessionContext


def _account(account_id: str, cookie_id: str) -> AccountMeta:
    return AccountMeta(
        id=account_id,
        name="虚构账号",
        email=f"{account_id}@example.test",
        created_at="2026-11-13T00:00:00Z",
        cookie_id=cookie_id,
        tier="Pro",
        tier_checked_at=time.time(),
    )


def _context(account: AccountMeta, cookie_id: str) -> SessionContext:
    ctx = SessionContext(
        auth_file=str(Path("virtual") / account.id / "auth.json"),
        auth_user="0",
        cookies={"SID": "synthetic_sid", "__Secure-1PSIDTS": "synthetic_ts"},
    )
    ctx.cookie_id = cookie_id
    return ctx


def _prepared(ctx: SessionContext) -> PreparedRequest:
    return PreparedRequest(
        url="https://example.test/rpc",
        headers={},
        body="[]",
        model="models/synthetic-model",
        snapshot="!synthetic",
        ctx=ctx,
    )


def _normalized(*_args: object) -> NormalizedGeminiRequest:
    return NormalizedGeminiRequest(
        model="models/synthetic-model",
        contents=[],
        system_instruction=None,
        tools=None,
        safety_settings=None,
    )


def _request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}]}
    )


def _service(accounts: list[AccountMeta], active_id: str) -> MagicMock:
    by_id = {account.id: account for account in accounts}
    service = MagicMock()
    service.get_active_account.side_effect = lambda: by_id[active_id]
    service.get_account.side_effect = by_id.get
    service.get_account_auth_path.side_effect = lambda account_id: (
        Path("virtual") / account_id / "auth.json"
    )
    service.is_cookie_generation_current.side_effect = lambda account_id, cookie_id: (
        account_id in by_id and (by_id[account_id].cookie_id or "") == (cookie_id or "")
    )
    service.rotate_account_cookies = AsyncMock(
        return_value={"ok": False, "status": 0, "skipped": "missing_seed"}
    )
    return service


def _bind_runtime(monkeypatch, service: MagicMock, client: MagicMock) -> list[str]:
    from aistudio_api.infrastructure.account import cookie_parser

    expired: list[str] = []

    async def switch_account(
        *, failed_account_id=None, is_session_expired=False, **_kwargs
    ):
        if is_session_expired:
            assert isinstance(failed_account_id, str)
            expired.append(failed_account_id)
        return False

    monkeypatch.setattr(gemini.runtime_state, "account_service", service)
    monkeypatch.setattr(gemini.runtime_state, "client", client)
    monkeypatch.setattr(gemini.runtime_state, "record", MagicMock())
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini, "try_switch_account", switch_account)
    monkeypatch.setattr(gemini, "record_rotator_event", MagicMock())
    monkeypatch.setattr(gemini, "normalize_gemini_request", _normalized)
    monkeypatch.setattr(
        cookie_parser,
        "load_cookies_from_auth_file",
        lambda _path: {"SID": "synthetic_sid", "__Secure-1PSIDTS": "synthetic_ts"},
    )
    monkeypatch.setattr(
        cookie_parser,
        "probe_aistudio_session",
        AsyncMock(return_value=(False, "signed_out")),
    )
    return expired


@pytest.mark.asyncio
@pytest.mark.parametrize("old_cookie_id", ["", "cookie_old"])
@pytest.mark.parametrize("stream", [False, True])
async def test_old_request_cannot_expire_reimported_account(
    monkeypatch, old_cookie_id, stream
):
    account = _account("acc_a", "cookie_new")
    old_ctx = _context(account, old_cookie_id)
    new_ctx = _context(account, "cookie_new")
    client = MagicMock()
    client.session.active_context = old_ctx
    client.prepare_request = AsyncMock(
        side_effect=[_prepared(old_ctx), _prepared(new_ctx)]
    )
    client.generate_content = AsyncMock(
        side_effect=[
            SessionExpiredError("虚构旧请求的迟到认证失败"),
            ModelOutput(candidates=[Candidate(text="synthetic_ok", finish_reason=1)]),
        ]
    )
    stream_calls = 0

    async def stream_content(**_kwargs):
        nonlocal stream_calls
        stream_calls += 1
        if stream_calls == 1:
            raise SessionExpiredError("虚构旧流式请求的迟到认证失败")
        yield "body", "synthetic_ok"
        yield "finish_reason", 1
        yield "usage", {}

    client.stream_generate_content = stream_content
    service = _service([account], account.id)
    expired = _bind_runtime(monkeypatch, service, client)

    result = await gemini.handle_gemini_generate_content(
        "synthetic-model", _request(), client, stream=stream
    )

    if stream:
        assert isinstance(result, StreamingResponse)
        chunks = [chunk async for chunk in result.body_iterator]
        payload = "".join(
            chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8")
            for chunk in chunks
        )
        assert "synthetic_ok" in payload
        assert '"error"' not in payload
    else:
        assert isinstance(result, GeminiGenerateContentResponse)
        assert result.candidates[0].content.parts[0].text == "synthetic_ok"
    assert expired == []
    service.rotate_account_cookies.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_failure_is_attributed_to_its_request_context(monkeypatch):
    account_a = _account("acc_a", "cookie_a")
    account_b = _account("acc_b", "cookie_b")
    request_ctx = _context(account_a, "cookie_a")
    client = MagicMock()
    client.session.active_context = _context(account_b, "cookie_b")
    client.prepare_request = AsyncMock(return_value=_prepared(request_ctx))
    client.generate_content = AsyncMock(side_effect=SessionExpiredError("虚构认证失败"))
    service = _service([account_a, account_b], account_b.id)
    expired = _bind_runtime(monkeypatch, service, client)

    with pytest.raises(HTTPException) as error:
        await gemini.handle_gemini_generate_content(
            "synthetic-model", _request(), client, stream=False
        )

    assert error.value.status_code == 401
    assert expired == [account_a.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("rotation_status", [401, 200])
async def test_old_background_probe_cannot_expire_new_generation(
    monkeypatch, rotation_status
):
    from aistudio_api.application import account_rotator, account_service
    from aistudio_api.infrastructure.account import account_store, cookie_parser
    from aistudio_api.infrastructure.waa import daemon

    account = _account("acc_a", "cookie_old")
    cookies = {"SID": "synthetic_sid", "__Secure-1PSIDTS": "synthetic_ts"}
    store = MagicMock()
    store.list_accounts.return_value = [account]
    store.get_active_auth_path.return_value = None
    store.get_auth_path.return_value = Path("virtual/acc_a/auth.json")
    service = _service([account], account.id)
    service.rotate_account_cookies.return_value = {
        "ok": rotation_status == 200,
        "status": rotation_status,
        "cookies": cookies,
    }
    rotator = MagicMock()
    rotator.get_account_stats.return_value = SimpleNamespace(session_expired=False)
    client = MagicMock()
    client.close = AsyncMock()
    probe_started = asyncio.Event()
    finish_probe = asyncio.Event()
    cycle_finished = asyncio.Event()
    stay_idle = asyncio.Event()
    tasks: list[asyncio.Task[object]] = []
    original_create_task = asyncio.create_task

    async def probe(*_args, **_kwargs):
        probe_started.set()
        await finish_probe.wait()
        return False, "signed_out"

    async def controlled_sleep(delay):
        if delay == 5.0:
            return
        cycle_finished.set()
        await stay_idle.wait()

    def capture_task(coroutine):
        task = original_create_task(coroutine)
        tasks.append(task)
        return task

    monkeypatch.setattr(daemon, "ensure_signer_ready", lambda: None)
    monkeypatch.setattr(daemon, "stop_signer_daemon", lambda: None)
    monkeypatch.setattr(account_store, "AccountStore", lambda: store)
    monkeypatch.setattr(account_service, "AccountService", lambda _store: service)
    monkeypatch.setattr(account_rotator, "init_rotator", lambda _store: rotator)
    monkeypatch.setattr(app_module, "AIStudioClient", lambda: client)
    monkeypatch.setattr(
        cookie_parser, "load_cookies_from_auth_file", lambda _path: dict(cookies)
    )
    monkeypatch.setattr(cookie_parser, "probe_aistudio_session", probe)
    # 只替换应用模块的引用，避免第三方模块在导入时捕获测试中的 sleep。
    monkeypatch.setattr(
        app_module,
        "asyncio",
        SimpleNamespace(
            sleep=controlled_sleep,
            create_task=capture_task,
            Semaphore=asyncio.Semaphore,
            gather=asyncio.gather,
            CancelledError=asyncio.CancelledError,
        ),
    )

    try:
        async with app_module.lifespan(app_module.app):
            await asyncio.wait_for(probe_started.wait(), timeout=2.0)
            # 旧探活尚未结束时模拟重新导入，确保结果携带旧代际。
            account.cookie_id = "cookie_new"
            finish_probe.set()
            await asyncio.wait_for(cycle_finished.wait(), timeout=2.0)
            rotator.record_session_expired.assert_not_called()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_cookie_id", ["", "cookie_old"])
async def test_reimport_while_waiting_for_switch_lock_is_not_isolated(
    monkeypatch, old_cookie_id
):
    from aistudio_api.application import account_orchestrator

    account = _account("acc_a", old_cookie_id)
    service = _service([account], account.id)
    service.activate_account = AsyncMock()
    rotator = MagicMock()
    rotator.is_account_available.return_value = True
    rotator.get_next_account = AsyncMock()
    client = MagicMock()
    lock = asyncio.Lock()
    await lock.acquire()
    monkeypatch.setattr(account_orchestrator.runtime_state, "account_service", service)
    monkeypatch.setattr(account_orchestrator.runtime_state, "rotator", rotator)
    monkeypatch.setattr(account_orchestrator.runtime_state, "client", client)
    monkeypatch.setattr(account_orchestrator, "_get_switch_lock", lambda: lock)
    monkeypatch.setattr(account_orchestrator, "_last_switch_time", 0.0)

    task = asyncio.create_task(
        account_orchestrator.try_switch_account(
            failed_account_id=account.id,
            is_session_expired=True,
            expected_cookie_id=old_cookie_id,
        )
    )
    try:
        await asyncio.sleep(0)
        assert not task.done()
        # 在切号锁被占用时重新导入，锁内必须再次确认代际。
        account.cookie_id = "cookie_new"
        lock.release()
        assert await asyncio.wait_for(task, timeout=2.0) is True
        rotator.record_session_expired.assert_not_called()
        rotator.get_next_account.assert_not_awaited()
        service.activate_account.assert_not_awaited()
    finally:
        if lock.locked():
            lock.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_session_before_preparation_retries_without_auth_penalty(
    monkeypatch,
):
    from aistudio_api.domain.errors import StaleSessionError

    account = _account("acc_a", "cookie_new")
    client = MagicMock()
    client.prepare_request = AsyncMock(
        side_effect=[
            StaleSessionError("虚构的过期签名上下文"),
            _prepared(_context(account, "cookie_new")),
        ]
    )
    client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[Candidate(text="synthetic_ok", finish_reason=1)]
        )
    )
    service = _service([account], account.id)
    expired = _bind_runtime(monkeypatch, service, client)

    result = await gemini.handle_gemini_generate_content(
        "synthetic-model", _request(), client, stream=False
    )

    assert isinstance(result, GeminiGenerateContentResponse)
    assert result.candidates[0].content.parts[0].text == "synthetic_ok"
    assert client.prepare_request.await_count == 2
    assert expired == []
    service.rotate_account_cookies.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change_generation,stale_result", [(False, True), (True, False)]
)
async def test_rotation_result_from_old_generation_is_ignored(
    monkeypatch, change_generation, stale_result
):
    from aistudio_api.infrastructure.account import cookie_parser

    account = _account("acc_a", "cookie_old")
    ctx = _context(account, "cookie_old")
    service = _service([account], account.id)
    client = MagicMock()
    expired = _bind_runtime(monkeypatch, service, client)
    monkeypatch.setattr(
        cookie_parser, "probe_aistudio_session", AsyncMock(return_value=(True, "ok"))
    )

    async def rotate(*_args, **_kwargs):
        if change_generation:
            account.cookie_id = "cookie_new"
        return {"ok": False, "status": 401, "stale": stale_result}

    service.rotate_account_cookies.side_effect = rotate

    assert (
        await gemini.handle_attempt_exception(
            SessionExpiredError("虚构认证错误"),
            attempt=0,
            model_path="synthetic-model",
            normalized_model="models/synthetic-model",
            request_ctx=ctx,
        )
        is True
    )
    assert expired == []


@pytest.mark.asyncio
async def test_legacy_auth_handler_without_context_preserves_expiration_behavior(
    monkeypatch,
):
    account = _account("acc_a", "cookie_current")
    service = _service([account], account.id)
    expired = _bind_runtime(monkeypatch, service, MagicMock())

    with pytest.raises(HTTPException) as error:
        await gemini.handle_attempt_exception(
            SessionExpiredError("虚构认证错误"),
            attempt=0,
            model_path="synthetic-model",
            normalized_model="models/synthetic-model",
        )

    assert error.value.status_code == 401
    assert expired == [account.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cookie_ids,expected_ids",
    [
        (("cookie_a", "cookie_b"), ["acc_a", "acc_b"]),
        (("cookie_a", "cookie_a"), ["acc_a"]),
    ],
)
async def test_background_rotation_respects_cookie_generation_groups(
    monkeypatch, cookie_ids, expected_ids
):
    from aistudio_api.application import account_rotator, account_service
    from aistudio_api.infrastructure.account import account_store, cookie_parser
    from aistudio_api.infrastructure.waa import daemon

    accounts = [_account("acc_a", cookie_ids[0]), _account("acc_b", cookie_ids[1])]
    cookies = {"SID": "synthetic_shared_sid", "__Secure-1PSIDTS": "synthetic_ts"}
    store = MagicMock()
    store.list_accounts.return_value = accounts
    store.get_active_auth_path.return_value = None
    store.get_auth_path.side_effect = lambda account_id: (
        Path("virtual") / account_id / "auth.json"
    )
    service = _service(accounts, "acc_a")
    service.rotate_account_cookies.return_value = {"ok": False, "status": 0}
    rotator = MagicMock()
    rotator.get_account_stats.return_value = SimpleNamespace(session_expired=False)
    client = MagicMock()
    client.close = AsyncMock()
    cycle_finished = asyncio.Event()
    stay_idle = asyncio.Event()
    tasks: list[asyncio.Task[object]] = []
    original_create_task = asyncio.create_task

    async def controlled_sleep(delay):
        if delay == 5.0:
            return
        cycle_finished.set()
        await stay_idle.wait()

    def capture_task(coroutine):
        task = original_create_task(coroutine)
        tasks.append(task)
        return task

    monkeypatch.setattr(daemon, "ensure_signer_ready", lambda: None)
    monkeypatch.setattr(daemon, "stop_signer_daemon", lambda: None)
    monkeypatch.setattr(account_store, "AccountStore", lambda: store)
    monkeypatch.setattr(account_service, "AccountService", lambda _store: service)
    monkeypatch.setattr(account_rotator, "init_rotator", lambda _store: rotator)
    monkeypatch.setattr(app_module, "AIStudioClient", lambda: client)
    monkeypatch.setattr(
        cookie_parser, "load_cookies_from_auth_file", lambda _path: dict(cookies)
    )
    monkeypatch.setattr(
        app_module,
        "asyncio",
        SimpleNamespace(
            sleep=controlled_sleep,
            create_task=capture_task,
            Semaphore=asyncio.Semaphore,
            gather=asyncio.gather,
            CancelledError=asyncio.CancelledError,
        ),
    )
    try:
        async with app_module.lifespan(app_module.app):
            await asyncio.wait_for(cycle_finished.wait(), timeout=2.0)
            called_ids = [
                call.args[0] for call in service.rotate_account_cookies.await_args_list
            ]
            # 同源不同代际全部轮换，同源同代际只执行一次。
            assert called_ids == expected_ids
            rotator.record_session_expired.assert_not_called()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
