"""Structured imports must retain browser cookie scope and lifetime metadata."""

import copy
import json
from unittest.mock import AsyncMock

import pytest

from aistudio_api.infrastructure.account.cookie_parser import parse_cookie_string
from aistudio_api.infrastructure.browser.cdp_client import CDPConnection, CDPPage


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_structured_import_preserves_distinct_scopes(as_json, wrapped):
    cookies = [
        {
            "name": "SID",
            "value": "synthetic-root",
            "domain": ".google.com",
            "path": "/",
            "expires": 4102444800,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "SID",
            "value": "synthetic-accounts",
            "domain": "accounts.google.com",
            "path": "/",
        },
        {
            "name": "SID",
            "value": "synthetic-path",
            "domain": "accounts.google.com",
            "path": "/ServiceLogin",
        },
        {
            "name": "__Host-1PLSID",
            "value": "synthetic-login",
            "domain": "accounts.google.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
        },
    ]
    origins = [{"origin": "https://aistudio.google.com", "localStorage": []}]
    raw = {"cookies": cookies, "origins": origins} if wrapped else cookies
    original = copy.deepcopy(raw)

    state = parse_cookie_string(json.dumps(raw) if as_json else raw)

    assert state == {"cookies": cookies, "origins": origins if wrapped else []}
    assert raw == original


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"expirationDate": 4102444800}, {"expires": 4102444800}),
        ({"expirationDate": 4102444800, "expires": 123}, {"expires": 123}),
        ({"expires": -1}, {}),
        ({"session": True, "expirationDate": 4102444800}, {"session": True}),
        ({"session": True, "expires": 4102444800}, {"session": True}),
        ({"sameSite": "no_restriction"}, {"sameSite": "None"}),
        ({"sameSite": "unspecified"}, {}),
        ({"sameSite": "lax"}, {"sameSite": "Lax"}),
    ],
)
def test_extension_metadata_is_normalized_without_extending_lifetime(
    metadata, expected
):
    cookie = {"name": "SID", "value": "synthetic", "domain": ".google.com"}
    raw = [{**cookie, **metadata}]
    original = copy.deepcopy(raw)

    assert parse_cookie_string(raw)["cookies"] == [{**cookie, **expected}]
    assert raw == original


@pytest.mark.parametrize("discard_transient", [False, True])
def test_structured_import_respects_transient_filter(discard_transient):
    names = ["SID", "__Host-1PLSID", "OSID", "SIDCC", "_ga_TEST", "_gcl_test"]
    cookies = [
        {"name": name, "value": "synthetic", "domain": "accounts.google.com"}
        for name in names
    ]
    state = parse_cookie_string(cookies, discard_transient=discard_transient)

    assert state["cookies"] == (cookies[:2] if discard_transient else cookies)


def test_scoped_and_unscoped_entries_can_be_imported_together():
    scoped = {
        "name": "LSID",
        "value": "synthetic-login",
        "url": "https://accounts.google.com/",
    }
    raw = [scoped, {"name": "SID", "value": "synthetic-root"}, {}, None]
    state = parse_cookie_string(raw)
    cookies = state["cookies"]

    assert isinstance(cookies, list)
    assert len(cookies) == 2
    assert cookies[0] == scoped
    assert cookies[1]["name"] == "SID"
    assert cookies[1]["domain"] == ".google.com"
    assert cookies[1]["path"] == "/"


@pytest.mark.parametrize(
    "raw",
    [
        "Cookie: SID=synthetic; OSID=stale",
        {"cookies": "SID=synthetic; OSID=stale"},
        {"cookies": {"SID": "synthetic", "OSID": "stale"}},
        [{"name": "SID", "value": "synthetic"}, {"name": "OSID", "value": "stale"}],
    ],
)
def test_unscoped_import_formats_keep_legacy_defaults(raw, monkeypatch):
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.time.time", lambda: 1000
    )

    assert parse_cookie_string(raw) == {
        "cookies": [
            {
                "name": "SID",
                "value": "synthetic",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "None",
                "expires": 15553000,
            }
        ],
        "origins": [],
    }


@pytest.mark.asyncio
async def test_imported_host_session_cookie_reaches_cdp_without_expiry(monkeypatch):
    state = parse_cookie_string(
        [
            {
                "name": "__Host-1PLSID",
                "value": "synthetic-login",
                "domain": "accounts.google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": -1,
            }
        ]
    )
    connection = CDPConnection("ws://unused.test")
    send = AsyncMock(return_value={})
    monkeypatch.setattr(connection, "send", send)
    page = CDPPage(connection, "synthetic-target")
    cookies = state["cookies"]
    assert isinstance(cookies, list)

    await page.set_cookies(cookies)

    send.assert_awaited_once_with(
        "Network.setCookies",
        {
            "cookies": [
                {
                    "name": "__Host-1PLSID",
                    "value": "synthetic-login",
                    "url": "https://accounts.google.com/",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                }
            ]
        },
    )
