"""Unit tests for Google cookie persistence across browser restarts.

Covers the four write-back defects that make a session die 1~2 hours after a
container restart: dropped session cookies, domain-scoped scrapes overwriting
the persisted jar, missing ``__Secure-1PSIDTS`` write-back, and a blanket
``clearBrowserCookies`` that destroys the native profile binding.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aistudio_api.infrastructure.browser.cdp_client import (
    COOKIE_QUERY_URLS,
    CDPPage,
)
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_auth import (
    load_cookies_to_page,
    merge_cookie_sets,
    save_browser_cookies,
)


class _FakeCDP:
    """Minimal CDP connection stub recording every JSON-RPC call."""

    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []
        self._closed = False
        self._responses = responses or {}

    async def send(
        self,
        method: str,
        params: dict[str, object] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, object]:
        self.sent.append((method, params or {}))
        handler = self._responses.get(method)
        if isinstance(handler, Exception):
            raise handler
        if callable(handler):
            return handler(params or {})  # type: ignore[return-value]
        if isinstance(handler, dict):
            return handler
        return {}

    def calls(self, method: str) -> list[dict[str, object]]:
        return [params for name, params in self.sent if name == method]


def _page_with(cdp: _FakeCDP, url: str = "https://aistudio.google.com/app") -> CDPPage:
    page = CDPPage(cdp, "target-1")  # type: ignore[arg-type]
    page._last_url = url
    return page


def _write_auth(path, cookies: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")


# --------------------------------------------------------------------------
# Defect 1: expires=-1 handed to Network.setCookies deletes session cookies
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_cookies_omits_non_positive_expiry():
    cdp = _FakeCDP()
    page = _page_with(cdp)

    await page.set_cookies(
        [
            {
                "name": "OSID",
                "value": "session",
                "domain": ".google.com",
                "expires": -1,
            },
            {
                "name": "SID",
                "value": "persist",
                "domain": ".google.com",
                "expires": 1_900_000_000,
            },
        ]
    )

    payload = cdp.calls("Network.setCookies")[0]["cookies"]
    by_name = {item["name"]: item for item in payload}
    assert "expires" not in by_name["OSID"], "session cookies must stay session cookies"
    assert by_name["SID"]["expires"] == 1_900_000_000.0


@pytest.mark.asyncio
async def test_set_cookies_tolerates_garbage_expiry():
    cdp = _FakeCDP()
    page = _page_with(cdp)

    await page.set_cookies([{"name": "NID", "value": "x", "expires": "not-a-date"}])

    payload = cdp.calls("Network.setCookies")[0]["cookies"]
    assert payload[0]["name"] == "NID"
    assert "expires" not in payload[0]


# --------------------------------------------------------------------------
# Defect 2: page-scoped cookie scrape loses accounts.google.com credentials
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cookies_prefers_browser_scoped_storage_domain():
    jar = [
        {"name": "LSID", "value": "root", "domain": "accounts.google.com", "path": "/"},
        {"name": "SID", "value": "aistudio", "domain": ".google.com", "path": "/"},
    ]
    cdp = _FakeCDP({"Storage.getCookies": {"cookies": jar}})

    cookies = await _page_with(cdp).get_cookies()

    assert {c["name"] for c in cookies} == {"LSID", "SID"}
    assert cdp.calls("Network.getCookies") == []


@pytest.mark.asyncio
async def test_get_cookies_falls_back_to_multi_domain_network_query():
    cdp = _FakeCDP(
        {
            "Storage.getCookies": RuntimeError("Storage domain unavailable"),
            "Network.getCookies": {
                "cookies": [
                    {
                        "name": "__Host-GAPS",
                        "value": "root",
                        "domain": "accounts.google.com",
                    }
                ]
            },
        }
    )

    cookies = await _page_with(cdp).get_cookies()

    assert [c["name"] for c in cookies] == ["__Host-GAPS"]
    query_urls = cdp.calls("Network.getCookies")[0]["urls"]
    assert "https://accounts.google.com/" in query_urls
    assert set(query_urls) == set(COOKIE_QUERY_URLS)


# --------------------------------------------------------------------------
# Defect 3: full overwrite of auth.json with a partial scrape
# --------------------------------------------------------------------------


def test_merge_cookie_sets_keeps_unscraped_root_credentials():
    existing = [
        {"name": "LSID", "value": "root", "domain": "accounts.google.com", "path": "/"},
        {
            "name": "__Secure-1PSIDTS",
            "value": "old",
            "domain": ".google.com",
            "path": "/",
        },
    ]
    fresh = [
        {
            "name": "__Secure-1PSIDTS",
            "value": "new",
            "domain": ".google.com",
            "path": "/",
        },
        {"name": "SID", "value": "s", "domain": ".google.com", "path": "/"},
    ]

    merged = merge_cookie_sets(existing, fresh)
    by_name = {c["name"]: c["value"] for c in merged}

    assert by_name["LSID"] == "root"
    assert by_name["__Secure-1PSIDTS"] == "new"
    assert by_name["SID"] == "s"
    assert len(merged) == 3


def test_merge_cookie_sets_drops_expired_persisted_cookies():
    expired = int(time.time()) - 60
    merged = merge_cookie_sets(
        [{"name": "STALE", "value": "x", "domain": ".google.com", "expires": expired}],
        [],
    )
    assert merged == []


@pytest.mark.asyncio
async def test_save_browser_cookies_merges_instead_of_overwriting(tmp_path):
    auth_file = tmp_path / "acc" / "auth.json"
    auth_file.parent.mkdir()
    _write_auth(
        auth_file,
        [
            {
                "name": "LSID",
                "value": "root",
                "domain": "accounts.google.com",
                "path": "/",
            },
            {"name": "SID", "value": "old", "domain": ".google.com", "path": "/"},
        ],
    )
    cdp = _FakeCDP(
        {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "rotated",
                        "domain": ".google.com",
                        "path": "/",
                    }
                ]
            }
        }
    )
    page = _page_with(cdp)

    await save_browser_cookies(page, str(auth_file))

    saved = json.loads(auth_file.read_text(encoding="utf-8"))["cookies"]
    by_name = {c["name"]: c["value"] for c in saved}
    assert by_name["LSID"] == "root", "accounts.google.com root credential was dropped"
    assert by_name["SID"] == "rotated"


@pytest.mark.asyncio
async def test_save_browser_cookies_skips_login_page(tmp_path):
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file, [{"name": "SID", "value": "keep", "domain": ".google.com"}])
    original = auth_file.read_text(encoding="utf-8")

    cdp = _FakeCDP({"Storage.getCookies": {"cookies": []}})
    page = _page_with(
        cdp, url="https://accounts.google.com/v3/signin/identifier?continue=x"
    )

    await save_browser_cookies(page, str(auth_file))

    assert auth_file.read_text(encoding="utf-8") == original
    assert cdp.calls("Storage.getCookies") == []


# --------------------------------------------------------------------------
# Defect 4: unconditional clearBrowserCookies wipes the native profile
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_cookies_does_not_clear_on_same_account_reload(tmp_path):
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file, [{"name": "SID", "value": "s", "domain": ".google.com"}])
    cdp = _FakeCDP()
    page = _page_with(cdp)

    assert await load_cookies_to_page(page, str(auth_file)) is True

    assert cdp.calls("Network.clearBrowserCookies") == []
    assert len(cdp.calls("Network.setCookies")) == 1


@pytest.mark.asyncio
async def test_load_cookies_clears_when_switching_accounts(tmp_path):
    first = tmp_path / "account_a" / "auth.json"
    second = tmp_path / "account_b" / "auth.json"
    for path in (first, second):
        path.parent.mkdir()
        _write_auth(path, [{"name": "SID", "value": "s", "domain": ".google.com"}])

    cdp = _FakeCDP()
    page = _page_with(cdp)
    await load_cookies_to_page(page, str(first))
    cdp.sent.clear()

    assert await load_cookies_to_page(page, str(second), switching_account=True) is True

    assert len(cdp.calls("Network.clearBrowserCookies")) == 1
    assert len(cdp.calls("Network.setCookies")) == 1


@pytest.mark.asyncio
async def test_load_cookies_skips_overwrite_when_profile_session_is_live(tmp_path):
    auth_file = tmp_path / "auth.json"
    _write_auth(
        auth_file, [{"name": "SID", "value": "from_disk", "domain": ".google.com"}]
    )
    cdp = _FakeCDP(
        {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "__Secure-1PSID",
                        "value": "native",
                        "domain": ".google.com",
                        "expires": -1,
                    }
                ]
            }
        }
    )
    page = _page_with(cdp)

    assert await load_cookies_to_page(page, str(auth_file)) is True

    assert cdp.calls("Network.setCookies") == [], (
        "native DBSC-bound session was overwritten"
    )


@pytest.mark.asyncio
async def test_load_cookies_injects_when_profile_credentials_expired(tmp_path):
    auth_file = tmp_path / "auth.json"
    _write_auth(
        auth_file, [{"name": "SID", "value": "from_disk", "domain": ".google.com"}]
    )
    cdp = _FakeCDP(
        {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "stale",
                        "domain": ".google.com",
                        "expires": int(time.time()) - 3600,
                    }
                ]
            }
        }
    )
    page = _page_with(cdp)

    assert await load_cookies_to_page(page, str(auth_file)) is True

    assert len(cdp.calls("Network.setCookies")) == 1


# --------------------------------------------------------------------------
# Rolling __Secure-1PSIDTS must reach disk, otherwise restarts re-seed stale
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_stream_page_writes_rotated_cookies_back(tmp_path):
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file, [{"name": "SID", "value": "s", "domain": ".google.com"}])

    session = BrowserSession(port=9222)
    session._auth_file = str(auth_file)
    session._in_flight = 0
    session._stream_cleanup_count = 4  # next increment hits % 5 == 0

    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    page._last_url = "https://aistudio.google.com/prompts/new_chat"
    page.evaluate = AsyncMock()
    page.collect_garbage = AsyncMock()

    with patch(
        "aistudio_api.infrastructure.gateway.session.save_browser_cookies",
        AsyncMock(),
    ) as save_mock:
        await session.cleanup_stream_page(page)

    save_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_cookies_injects_when_page_on_login_url(tmp_path):
    auth_file = tmp_path / "auth.json"
    _write_auth(
        auth_file, [{"name": "SID", "value": "from_disk", "domain": ".google.com"}]
    )
    cdp = _FakeCDP(
        {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "stale",
                        "domain": ".google.com",
                        "expires": int(time.time()) + 3600,
                    }
                ]
            }
        }
    )
    page = _page_with(cdp)
    page._last_url = "https://accounts.google.com/v3/signin/identifier"

    assert await load_cookies_to_page(page, str(auth_file)) is True
    assert len(cdp.calls("Network.setCookies")) == 1


@pytest.mark.asyncio
async def test_save_browser_cookies_syncs_to_sibling_accounts(tmp_path):
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir()

    # acc_1 (target)
    acc1_dir = accounts_dir / "acc_1"
    acc1_dir.mkdir()
    (acc1_dir / "meta.json").write_text(json.dumps({"id": "acc_1", "cookie_id": "group_shared"}))
    acc1_auth = acc1_dir / "auth.json"
    _write_auth(acc1_auth, [{"name": "SID", "value": "old_sid", "domain": ".google.com"}])

    # acc_2 (sibling with same cookie_id)
    acc2_dir = accounts_dir / "acc_2"
    acc2_dir.mkdir()
    (acc2_dir / "meta.json").write_text(json.dumps({"id": "acc_2", "cookie_id": "group_shared"}))
    acc2_auth = acc2_dir / "auth.json"
    _write_auth(acc2_auth, [{"name": "SID", "value": "old_sid", "domain": ".google.com"}])

    # acc_3 (different cookie_id)
    acc3_dir = accounts_dir / "acc_3"
    acc3_dir.mkdir()
    (acc3_dir / "meta.json").write_text(json.dumps({"id": "acc_3", "cookie_id": "group_different"}))
    acc3_auth = acc3_dir / "auth.json"
    _write_auth(acc3_auth, [{"name": "SID", "value": "diff_sid", "domain": ".google.com"}])

    cdp = _FakeCDP(
        {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "rolled_sid",
                        "domain": ".google.com",
                        "expires": int(time.time()) + 3600,
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "ts_fresh_123",
                        "domain": ".google.com",
                        "expires": int(time.time()) + 3600,
                    },
                ]
            }
        }
    )
    page = _page_with(cdp)

    await save_browser_cookies(page, str(acc1_auth))

    # Verify acc_1 was updated
    data1 = json.loads(acc1_auth.read_text())
    names1 = {c["name"]: c["value"] for c in data1["cookies"]}
    assert names1["SID"] == "rolled_sid"
    assert names1["__Secure-1PSIDTS"] == "ts_fresh_123"

    # Verify acc_2 (sibling) was also updated with fresh rolled tokens
    data2 = json.loads(acc2_auth.read_text())
    names2 = {c["name"]: c["value"] for c in data2["cookies"]}
    assert names2["SID"] == "rolled_sid"
    assert names2["__Secure-1PSIDTS"] == "ts_fresh_123"

    # Verify acc_3 (different group) was untouched
    data3 = json.loads(acc3_auth.read_text())
    names3 = {c["name"]: c["value"] for c in data3["cookies"]}
    assert names3["SID"] == "diff_sid"
    assert "__Secure-1PSIDTS" not in names3


@pytest.mark.asyncio
async def test_load_cookies_skips_clear_for_same_cookie_group(tmp_path):
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir()

    acc1_dir = accounts_dir / "acc_1"
    acc1_dir.mkdir()
    (acc1_dir / "meta.json").write_text(json.dumps({"id": "acc_1", "cookie_id": "group_shared"}))
    acc1_auth = acc1_dir / "auth.json"
    _write_auth(acc1_auth, [{"name": "SID", "value": "sid_val", "domain": ".google.com"}])

    acc2_dir = accounts_dir / "acc_2"
    acc2_dir.mkdir()
    (acc2_dir / "meta.json").write_text(json.dumps({"id": "acc_2", "cookie_id": "group_shared"}))
    acc2_auth = acc2_dir / "auth.json"
    _write_auth(acc2_auth, [{"name": "SID", "value": "sid_val", "domain": ".google.com"}])

    cdp = _FakeCDP(
        {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "sid_val",
                        "domain": ".google.com",
                        "expires": int(time.time()) + 3600,
                    }
                ]
            }
        }
    )
    page = _page_with(cdp)

    # Initial load for acc_1
    await load_cookies_to_page(page, str(acc1_auth))

    # Switching from acc_1 to acc_2 (same cookie_id)
    await load_cookies_to_page(
        page,
        str(acc2_auth),
        switching_account=True,
        previous_auth=str(acc1_auth),
    )

    # Should NOT have sent clearBrowserCookies
    assert len(cdp.calls("Network.clearBrowserCookies")) == 0


@pytest.mark.asyncio
async def test_load_cookies_clears_for_different_cookie_group(tmp_path):
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir()

    acc1_dir = accounts_dir / "acc_1"
    acc1_dir.mkdir()
    (acc1_dir / "meta.json").write_text(json.dumps({"id": "acc_1", "cookie_id": "group_A"}))
    acc1_auth = acc1_dir / "auth.json"
    _write_auth(acc1_auth, [{"name": "SID", "value": "sid_A", "domain": ".google.com"}])

    acc2_dir = accounts_dir / "acc_2"
    acc2_dir.mkdir()
    (acc2_dir / "meta.json").write_text(json.dumps({"id": "acc_2", "cookie_id": "group_B"}))
    acc2_auth = acc2_dir / "auth.json"
    _write_auth(acc2_auth, [{"name": "SID", "value": "sid_B", "domain": ".google.com"}])

    cdp = _FakeCDP(
        {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "sid_A",
                        "domain": ".google.com",
                        "expires": int(time.time()) + 3600,
                    }
                ]
            }
        }
    )
    page = _page_with(cdp)

    # Switching from acc_1 to acc_2 (different cookie_id)
    await load_cookies_to_page(
        page,
        str(acc2_auth),
        switching_account=True,
        previous_auth=str(acc1_auth),
    )

    # Must clear browser cookies when switching across different accounts
    assert len(cdp.calls("Network.clearBrowserCookies")) == 1


@pytest.mark.asyncio
async def test_browser_session_keepalive_tick(tmp_path):
    session = BrowserSession(port=9222)
    session._auth_file = str(tmp_path / "auth.json")
    _write_auth(Path(session._auth_file), [{"name": "SID", "value": "sid", "domain": ".google.com"}])

    # 1. Not ready -> False
    assert await session.keepalive_tick() is False

    # 2. Ready but active requests in flight -> False
    cdp = _FakeCDP({"Storage.getCookies": {"cookies": []}})
    page = _page_with(cdp)
    from aistudio_api.infrastructure.gateway.session_state import SessionContext
    ctx = SessionContext(
        page=page,
        snap_key="snap",
        auth_user="0",
        auth_file=session._auth_file,
        base_template={},
        tier="Free",
    )
    session.set_ready_context(ctx)
    session._in_flight = 1
    assert await session.keepalive_tick() is False

    # 3. Ready, no in-flight, but idle for < 15 min -> False
    session._in_flight = 0
    session._last_activity = time.time()
    assert await session.keepalive_tick() is False

    # 4. Ready, idle >= 15 min (e.g. 1000s) -> triggers keepalive
    session._last_activity = time.time() - 1000
    with patch(
        "aistudio_api.infrastructure.gateway.session.extract_account_tier",
        AsyncMock(return_value="Free"),
    ), patch(
        "aistudio_api.infrastructure.gateway.session.save_browser_cookies",
        AsyncMock(),
    ) as save_mock:
        assert await session.keepalive_tick() is True
        save_mock.assert_awaited_once()
        assert time.time() - session._last_activity < 5
