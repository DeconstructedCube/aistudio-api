"""Host-cookie imports must preserve credentials and report the saved result."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from aistudio_api.api.dependencies import get_account_service, get_runtime_state
from aistudio_api.api.routes_accounts import router
from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account import cookie_parser, cookie_refresher
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway import session as session_module
from aistudio_api.infrastructure.gateway.session import BrowserSession


def host_cookie(**fields):
    return {
        "name": "__Host-1PLSID",
        "value": "synthetic-host",
        "domain": "accounts.google.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "expires": -1,
        **fields,
    }


@pytest.fixture
def import_api(tmp_path, monkeypatch):
    service = AccountService(AccountStore(accounts_dir=tmp_path / "accounts"))
    runtime = RuntimeState(account_service=service)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_account_service] = lambda: service
    app.dependency_overrides[get_runtime_state] = lambda: runtime
    probe = AsyncMock(
        return_value=[{"auth_user": "0", "email": "test@example.test", "tier": "Free"}]
    )
    monkeypatch.setattr(cookie_parser, "probe_google_accounts_infinite", probe)
    return app, service, runtime, probe


async def post_import(app, cookies, endpoint="import-cookies"):
    raw = json.dumps(cookies)
    item = {"cookies": raw, "email": "test@example.test", "tier": "Free"}
    payload = {"accounts": [item]} if endpoint == "import-bundle" else item
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.post(f"/api/accounts/{endpoint}", json=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", ["import-cookies", "probe-import", "import-bundle"]
)
@pytest.mark.parametrize(
    "fields",
    [
        {"path": "/login"},
        {"domain": "", "url": "https://accounts.google.com/"},
        {"secure": False},
        {"domain": None, "url": "http://accounts.google.com/"},
    ],
)
async def test_invalid_host_cookie_is_rejected_before_probe_or_save(
    import_api, endpoint, fields
):
    app, service, _, probe = import_api
    response = await post_import(app, [host_cookie(**fields)], endpoint)
    assert response.status_code == 400
    assert "__Host-" in response.json()["detail"]
    assert "synthetic-host" not in response.text
    assert service.list_accounts() == []
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_bundle_does_not_save_earlier_valid_accounts(import_api):
    app, service, _, _ = import_api
    accounts = [
        {"cookies": json.dumps([cookie]), "email": "test@example.test", "tier": "Free"}
        for cookie in (host_cookie(), host_cookie(path="/login"))
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/accounts/import-bundle", json={"accounts": accounts}
        )
    assert response.status_code == 400
    assert service.list_accounts() == []


def test_refresher_keeps_host_metadata_and_existing_refresh_policy(monkeypatch):
    refresh = Mock(
        return_value={
            "SID": "synthetic-refreshed",
            "NEW": "synthetic-new",
            "OSID": "stale",
        }
    )
    monkeypatch.setattr(cookie_refresher, "_refresh_session_cookies", refresh)
    source = host_cookie()
    cookies = cookie_refresher.load_cookies_from_string(
        json.dumps([source, {"name": "SID", "value": "synthetic-old"}])
    )
    refresh.assert_called_once_with(
        {"__Host-1PLSID": "synthetic-host", "SID": "synthetic-old"}
    )
    by_name = {c["name"]: c for c in cookies}
    assert by_name["SID"]["value"] == "synthetic-refreshed"
    assert by_name["SID"]["domain"] == ".google.com"
    assert by_name["NEW"]["value"] == "synthetic-new"
    assert "OSID" not in by_name
    assert by_name["__Host-1PLSID"] == {
        k: v for k, v in source.items() if k != "expires"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "missing-host", "readback", "save"])
async def test_active_import_reports_persisted_browser_result(
    import_api, monkeypatch, failure
):
    app, service, runtime, _ = import_api
    refresh = Mock(return_value={"SID": "synthetic-refreshed", "NEW": "synthetic-new"})
    monkeypatch.setattr(cookie_refresher, "_refresh_session_cookies", refresh)
    exported_host = host_cookie(value="synthetic-rotated", session=True)
    page_cookies = [
        {"name": n, "value": v, "domain": ".google.com", "path": "/"}
        for n, v in [("SID", "synthetic-refreshed"), ("NEW", "synthetic-new")]
    ]
    cdp_send = AsyncMock(
        return_value={
            "cookies": []
            if failure == "missing-host"
            else [copy.deepcopy(exported_host)]
        }
    )
    if failure == "readback":
        cdp_send.side_effect = RuntimeError("synthetic-readback-failure")
    page = SimpleNamespace(
        set_cookies=AsyncMock(),
        get_cookies=AsyncMock(return_value=page_cookies),
        cdp=SimpleNamespace(send=cdp_send),
        close=AsyncMock(),
        target_id="synthetic-target",
    )
    session = BrowserSession(0)
    monkeypatch.setattr(session, "_ensure_browser_cdp", AsyncMock())
    monkeypatch.setattr(
        session,
        "_cdp_client",
        SimpleNamespace(
            create_new_page=AsyncMock(return_value=page), close_target=AsyncMock()
        ),
    )
    bootstrap = AsyncMock()
    monkeypatch.setattr(session_module, "bootstrap_google_session", bootstrap)
    if failure == "save":
        monkeypatch.setattr(session_module, "save_browser_cookies", AsyncMock())
    runtime.client = SimpleNamespace(_session=session)

    response = await post_import(
        app, [host_cookie(), {"name": "SID", "value": "synthetic-old"}]
    )
    refresh.assert_called_once()
    bootstrap.assert_awaited_once_with(page)
    page.close.assert_awaited_once()
    if failure:
        assert response.status_code == 502
        assert "synthetic-" not in response.text
        return
    assert response.status_code == 200
    saved = json.loads(
        service.get_account_auth_path(response.json()["account_id"]).read_text(
            encoding="utf-8"
        )
    )
    assert response.json()["cookie_count"] == len(saved["cookies"]) == 3
    assert response.json()["domain_summary"] == {
        ".google.com": 2,
        "accounts.google.com": 1,
    }
    assert next(c for c in saved["cookies"] if c["name"] == "__Host-1PLSID") == {
        k: v for k, v in exported_host.items() if k != "expires"
    }
    assert any(c["name"] == "__Host-1PLSID" for c in page.set_cookies.call_args.args[0])
    cdp_send.assert_awaited_once_with(
        "Network.getCookies", {"urls": ["https://accounts.google.com/"]}
    )
