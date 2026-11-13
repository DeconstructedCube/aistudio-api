"""验证导入新凭据后，旧请求与旧续期结果不能覆盖当前代际。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.routes_accounts import (
    BundleAccountItem,
    ImportBundleRequest,
    ImportCookiesRequest,
    ProbeAndImportRequest,
    import_bundle,
    import_cookies,
    probe_and_import,
)
from aistudio_api.application.account_service import AccountService
from aistudio_api.domain.errors import StaleSessionError, TransportError
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.account.cookie_parser import (
    load_cookies_from_auth_file,
)
from aistudio_api.infrastructure.gateway.session import GatewaySession
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart


def _state(marker: str) -> dict[str, object]:
    return {
        "cookies": [
            {"name": "SAPISID", "value": "same-fake-browser-session"},
            {"name": "SID", "value": f"sid-{marker}"},
            {"name": "__Secure-1PSIDTS", "value": f"ts-{marker}"},
        ]
    }


@pytest.fixture
def cookie_case(tmp_path, monkeypatch):
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("AISTUDIO_ACCOUNTS_DIR", str(accounts_dir))
    store = AccountStore(accounts_dir=accounts_dir)
    service = AccountService(store)
    account = store.save_account(
        name="测试账号",
        email="test-user@example.invalid",
        storage_state=_state("old"),
        cookie_id="generation-old",
        tier="Pro",
        group="production",
        notes="保留原备注",
    )
    session = GatewaySession()
    runtime = SimpleNamespace(
        client=SimpleNamespace(session=session), rotator=MagicMock()
    )
    return store, service, account, session, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("import_mode", ["probe", "bundle", "single"])
async def test_reimport_active_account_reloads_current_context(
    cookie_case, monkeypatch, import_mode
):
    store, service, account, session, runtime = cookie_case
    await service.activate_account(account.id, session)
    old_context = session.active_context
    fresh = "SAPISID=same-fake-browser-session; SID=sid-new; __Secure-1PSIDTS=ts-new"
    if import_mode == "probe":
        monkeypatch.setattr(
            "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
            AsyncMock(
                return_value=[
                    {
                        "auth_user": "0",
                        "email": account.email,
                        "name": account.name,
                        "tier": "Pro",
                    }
                ]
            ),
        )
        await probe_and_import(
            ProbeAndImportRequest(cookies=fresh),
            account_service=service,
            runtime_state=runtime,
        )
    elif import_mode == "bundle":
        await import_bundle(
            ImportBundleRequest(
                accounts=[
                    BundleAccountItem(
                        name=account.name,
                        email=account.email,
                        cookies=fresh,
                        auth_user="0",
                        tier="Pro",
                    )
                ]
            ),
            account_service=service,
            runtime_state=runtime,
        )
    else:
        await import_cookies(
            ImportCookiesRequest(
                cookies=fresh,
                account_id=account.id,
                email=account.email,
                tier="Pro",
            ),
            account_service=service,
            runtime_state=runtime,
        )
    auth_path = store.get_auth_path(account.id)
    assert auth_path is not None
    assert load_cookies_from_auth_file(auth_path)["SID"] == "sid-new"
    assert session.active_context is not None
    assert session.active_context.cookies["SID"] == "sid-new"
    assert session.active_context is not old_context
    assert len(store.list_accounts()) == 1
    saved = store.get_account(account.id)
    assert saved is not None
    assert saved.group == "production" and saved.notes == "保留原备注"


async def _reimport_new(service, account, session):
    service.save_account_from_cookies(
        name=account.name,
        email=account.email,
        storage_state=_state("new"),
        account_id=account.id,
        cookie_id="generation-new",
        tier="Pro",
    )
    await service.activate_account(account.id, session)


def _assert_new_cookie_survives(store, account, session):
    auth_path = store.get_auth_path(account.id)
    assert auth_path is not None
    disk_cookies = load_cookies_from_auth_file(auth_path)
    assert disk_cookies["SID"] == "sid-new"
    assert disk_cookies["__Secure-1PSIDTS"] == "ts-new"
    assert session.active_context is not None
    assert session.active_context.cookies["SID"] == "sid-new"
    assert session.active_context.cookies["__Secure-1PSIDTS"] == "ts-new"


@pytest.mark.asyncio
async def test_old_rotation_result_cannot_overwrite_reimport(cookie_case, monkeypatch):
    store, service, account, session, _runtime = cookie_case
    await service.activate_account(account.id, session)
    started, release = asyncio.Event(), asyncio.Event()

    async def fake_rotate(cookies, **_kwargs):
        started.set()
        await release.wait()
        cookies["__Secure-1PSIDTS"] = "rotated-from-old-generation"
        return {"ok": True, "status": 200, "updated": ["__Secure-1PSIDTS"]}

    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.rotate_google_cookies",
        fake_rotate,
    )
    task = asyncio.create_task(service.rotate_account_cookies(account.id, session))
    await started.wait()
    await _reimport_new(service, account, session)
    release.set()
    result = await task
    _assert_new_cookie_survives(store, account, session)
    assert result.get("stale") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_old_transport_response_cannot_overwrite_reimport(cookie_case, streaming):
    store, service, account, session, _runtime = cookie_case
    await service.activate_account(account.id, session)
    old_context = session.active_context
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(_request):
        started.set()
        await release.wait()
        return httpx.Response(
            200,
            headers={
                "Set-Cookie": "__Secure-1PSIDTS=old-response; Path=/; Domain=.google.com; Secure"
            },
            content=b"[]",
        )

    session._transport._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    async def send():
        if streaming:
            return [
                event
                async for event in session.send_streaming_request(
                    body="[]", url="https://example.invalid/mock", ctx=old_context
                )
            ]
        return await session.send_request(
            body="[]", url="https://example.invalid/mock", ctx=old_context
        )

    try:
        task = asyncio.create_task(send())
        await started.wait()
        await _reimport_new(service, account, session)
        release.set()
        await task
        _assert_new_cookie_survives(store, account, session)
    finally:
        release.set()
        await session.close()


@pytest.mark.asyncio
async def test_old_signer_response_cannot_overwrite_reimport(cookie_case):
    store, service, account, session, _runtime = cookie_case
    await service.activate_account(account.id, session)
    old_context = session.active_context
    started, release = asyncio.Event(), asyncio.Event()

    async def fake_sign(*_args, **_kwargs):
        started.set()
        await release.wait()
        return "!fake-proof"

    session._signer = SimpleNamespace(
        generate_snapshot=AsyncMock(side_effect=fake_sign),
        last_headers={"Cookie": "SID=sid-old; __Secure-1PSIDTS=old-signer-response"},
        close=AsyncMock(),
    )
    contents = [AistudioContent(role="user", parts=[AistudioPart(text="仅离线测试")])]
    task = asyncio.create_task(session.generate_snapshot(contents, ctx=old_context))
    await started.wait()
    await _reimport_new(service, account, session)
    release.set()
    with pytest.raises(StaleSessionError):
        await task
    _assert_new_cookie_survives(store, account, session)


@pytest.mark.asyncio
async def test_late_old_context_never_enters_signer(cookie_case):
    store, service, account, session, _runtime = cookie_case
    await service.activate_account(account.id, session)
    old_context = session.active_context
    await _reimport_new(service, account, session)
    signer = SimpleNamespace(generate_snapshot=AsyncMock(return_value="!fake-proof"))
    session._signer = signer
    contents = [AistudioContent(role="user", parts=[AistudioPart(text="离线测试")])]
    with pytest.raises(StaleSessionError):
        await session.generate_snapshot(contents, ctx=old_context)
    signer.generate_snapshot.assert_not_called()
    _assert_new_cookie_survives(store, account, session)


@pytest.mark.asyncio
async def test_signing_orders_generations_and_rejects_queued_old_context(cookie_case):
    store, service, account, session, _runtime = cookie_case
    await service.activate_account(account.id, session)
    old_context = session.active_context
    started, release = asyncio.Event(), asyncio.Event()
    entered_cookies = []

    async def fake_sign(*_args, **kwargs):
        entered_cookies.append(dict(kwargs["cookies"]))
        if len(entered_cookies) == 1:
            started.set()
            await release.wait()
        return "!fake-proof"

    session._signer = SimpleNamespace(
        generate_snapshot=AsyncMock(side_effect=fake_sign),
        last_headers={},
        close=AsyncMock(),
    )
    contents = [AistudioContent(role="user", parts=[AistudioPart(text="离线测试")])]
    old_task = asyncio.create_task(session.generate_snapshot(contents, ctx=old_context))
    await started.wait()
    await _reimport_new(service, account, session)
    queued_old = asyncio.create_task(
        session.generate_snapshot(contents, ctx=old_context)
    )
    new_task = asyncio.create_task(session.generate_snapshot(contents))
    await asyncio.sleep(0)
    assert len(entered_cookies) == 1
    release.set()
    with pytest.raises(StaleSessionError):
        await old_task
    with pytest.raises(StaleSessionError):
        await queued_old
    assert await new_task == "!fake-proof"
    assert [item["SID"] for item in entered_cookies] == ["sid-old", "sid-new"]
    _assert_new_cookie_survives(store, account, session)


def test_manual_reimport_wins_over_sibling_old_rotation_timestamp(cookie_case):
    store, _service, account, _session, _runtime = cookie_case
    sibling = store.save_account(
        name="同会话兄弟账号",
        email="sibling@example.invalid",
        storage_state=_state("old"),
        auth_user="1",
        cookie_id="generation-old",
    )
    sibling.last_rotated_at = 1234567890.0
    store.save_account(
        name=account.name,
        email=account.email,
        storage_state=_state("new"),
        account_id=account.id,
        cookie_id="generation-new",
    )
    auth_path = store.get_auth_path(account.id)
    assert auth_path is not None
    assert load_cookies_from_auth_file(auth_path)["__Secure-1PSIDTS"] == "ts-new"


def _create_old_sibling_after_reimport(store, account):
    sibling = store.save_account(
        name="旧代际兄弟账号",
        email="sibling@example.invalid",
        storage_state=_state("old"),
        auth_user="1",
        cookie_id="generation-old",
    )
    store.save_account(
        name=account.name,
        email=account.email,
        storage_state=_state("new"),
        account_id=account.id,
        cookie_id="generation-new",
        tier="Pro",
    )
    store.update_account_cookies(
        account_id=sibling.id,
        new_cookies={
            "SAPISID": "same-fake-browser-session",
            "SID": "sid-old",
            "__Secure-1PSIDTS": "ts-old",
        },
        rotated_at=1234567890.0,
        expected_cookie_id="generation-old",
    )
    return sibling


def test_store_reload_never_syncs_old_sibling_into_new_import(cookie_case, monkeypatch):
    store, _service, account, _session, _runtime = cookie_case
    _create_old_sibling_after_reimport(store, account)
    auth_path = store.get_auth_path(account.id)
    assert auth_path is not None
    assert load_cookies_from_auth_file(auth_path)["__Secure-1PSIDTS"] == "ts-new"
    monkeypatch.setattr(AccountStore, "_instances", {})
    fresh_store = AccountStore(accounts_dir=auth_path.parent.parent)
    fresh_store.list_accounts()
    assert load_cookies_from_auth_file(auth_path)["__Secure-1PSIDTS"] == "ts-new"


@pytest.mark.asyncio
async def test_rotation_does_not_borrow_old_generation_sibling(
    cookie_case, monkeypatch
):
    store, service, account, session, _runtime = cookie_case
    _create_old_sibling_after_reimport(store, account)
    await service.activate_account(account.id, session)
    attempted_cookies = []

    async def fake_rotate(cookies, **_kwargs):
        attempted_cookies.append(dict(cookies))
        if len(attempted_cookies) == 1:
            return {"ok": False, "status": 401, "updated": []}
        cookies["__Secure-1PSIDTS"] = "rotation-from-old-sibling"
        return {"ok": True, "status": 200, "updated": ["__Secure-1PSIDTS"]}

    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.rotate_google_cookies",
        fake_rotate,
    )
    result = await service.rotate_account_cookies(account.id, session)
    assert result["ok"] is False
    assert len(attempted_cookies) == 1
    _assert_new_cookie_survives(store, account, session)


def test_unrelated_manual_import_cannot_sync_other_generation_groups(cookie_case):
    store, _service, account, _session, _runtime = cookie_case
    _create_old_sibling_after_reimport(store, account)
    store.save_account(
        name="其他浏览器会话",
        email="unrelated@example.invalid",
        storage_state={"cookies": [{"name": "SAPISID", "value": "different-anchor"}]},
        cookie_id="unrelated-generation",
    )
    auth_path = store.get_auth_path(account.id)
    assert auth_path is not None
    assert load_cookies_from_auth_file(auth_path)["__Secure-1PSIDTS"] == "ts-new"


@pytest.mark.asyncio
async def test_old_signer_exception_becomes_stale_after_reimport(cookie_case):
    store, service, account, session, _runtime = cookie_case
    await service.activate_account(account.id, session)
    old_context = session.active_context
    started, release = asyncio.Event(), asyncio.Event()

    async def fake_sign(*_args, **_kwargs):
        started.set()
        await release.wait()
        raise TransportError("旧代际签名超时")

    session._signer = SimpleNamespace(
        generate_snapshot=AsyncMock(side_effect=fake_sign)
    )
    contents = [AistudioContent(role="user", parts=[AistudioPart(text="离线测试")])]
    task = asyncio.create_task(session.generate_snapshot(contents, ctx=old_context))
    await started.wait()
    await _reimport_new(service, account, session)
    release.set()
    with pytest.raises(StaleSessionError):
        await task
    _assert_new_cookie_survives(store, account, session)
