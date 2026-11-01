"""RotateCookies 前置检查（P1）的回归测试。

上游 aistudio-pg-bridge 的做法：缺 LSID / __Host-*PLSID 时直接跳过，
不要拿不完整的 cookie 去撞 RotateCookies——Google 可能反过来作废 SID 会话。
"""

from __future__ import annotations

import json
import logging
from typing import ClassVar

import httpx
import pytest

from aistudio_api.infrastructure.gateway import cookie_rotate as cr


def _cookie(name: str, value: str = "v", domain: str = ".google.com") -> dict:
    return {"name": name, "value": value, "domain": domain, "path": "/"}


BASE_COOKIES = [
    _cookie("SID"),
    _cookie("__Secure-1PSID"),
    _cookie("__Secure-1PSIDTS"),
    _cookie("__Host-GAPS", domain="accounts.google.com"),
]


class _FakeResponse:
    def __init__(self, status_code: int = 200, set_cookies: list[str] | None = None):
        self.status_code = status_code
        self.text = ""
        self.headers = httpx.Headers([("set-cookie", c) for c in (set_cookies or [])])


class _FakeClient:
    calls: ClassVar[list[tuple]] = []

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        type(self).calls.append((url, headers, json))
        return _FakeResponse(
            200, ["__Secure-1PSIDTS=rotated; Path=/; Secure; HttpOnly"]
        )


def test_has_rotate_credentials_handles_both_shapes():
    assert cr.has_rotate_credentials({"LSID": "x"}) is True
    assert cr.has_rotate_credentials({"SID": "x"}) is False
    assert cr.has_rotate_credentials(None) is False
    assert cr.has_rotate_credentials([_cookie("__Host-3PLSID")]) is True


@pytest.mark.asyncio
async def test_rotate_skips_when_lsid_missing(monkeypatch):
    """缺 LSID/__Host-*PLSID 时不应发起任何 HTTP 请求。"""

    def _boom(*a, **kw):
        raise AssertionError("不应该发起 HTTP 请求")

    monkeypatch.setattr(cr.httpx, "AsyncClient", _boom)

    ok, new_cookies, err = await cr.rotate_google_cookies_http(BASE_COOKIES)

    assert ok is False
    assert new_cookies == []
    assert err is not None and err.startswith(cr.SKIP_PREFIX)


@pytest.mark.asyncio
async def test_rotate_proceeds_when_lsid_present(monkeypatch):
    """带上 LSID 后正常发请求并解析 Set-Cookie。"""
    _FakeClient.calls = []
    monkeypatch.setattr(cr.httpx, "AsyncClient", _FakeClient)

    cookies = [*BASE_COOKIES, _cookie("LSID", domain="accounts.google.com")]
    ok, new_cookies, err = await cr.rotate_google_cookies_http(cookies)

    assert ok is True
    assert err is None
    assert [c["name"] for c in new_cookies] == ["__Secure-1PSIDTS"]
    assert len(_FakeClient.calls) == 1


@pytest.mark.asyncio
async def test_rotate_account_cookies_skips_with_info_log(
    monkeypatch, tmp_path, caplog
):
    """auth.json 缺 host-only cookie 时，按 info 记录并返回 False。"""
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"cookies": BASE_COOKIES, "updated_at": "2026-11-01T00:00:00Z"}),
        encoding="utf-8",
    )

    def _boom(*a, **kw):
        raise AssertionError("不应该发起 HTTP 请求")

    monkeypatch.setattr(cr.httpx, "AsyncClient", _boom)

    with caplog.at_level(logging.INFO):
        ok = await cr.rotate_account_cookies(auth)

    assert ok is False
    hits = [r for r in caplog.records if "跳过轮转" in r.getMessage()]
    assert len(hits) == 1
    assert hits[0].levelno == logging.INFO
