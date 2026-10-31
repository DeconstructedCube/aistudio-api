"""Unit tests for Google cookie persistence across browser restarts.

Covers the four write-back defects that make a session die 1~2 hours after a
container restart: dropped session cookies, domain-scoped scrapes overwriting
the persisted jar, missing ``__Secure-1PSIDTS`` write-back, and a blanket
CDP overwrites that downgrade a natively bound profile.
"""

from __future__ import annotations

import json
import time
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
# Defect 4: a CDP overwrite downgrades a natively bound profile
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
async def test_load_cookies_never_flushes_the_shared_jar(tmp_path):
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

    assert cdp.calls("Network.clearBrowserCookies") == []
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
    page.url = "https://aistudio.google.com/prompts/new_chat"
    page.evaluate = AsyncMock()
    page.collect_garbage = AsyncMock()

    with patch(
        "aistudio_api.infrastructure.gateway.session.save_browser_cookies",
        AsyncMock(),
    ) as save_mock:
        await session.cleanup_stream_page(page)

    save_mock.assert_awaited_once()
