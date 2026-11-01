import json
from unittest.mock import AsyncMock, patch

import pytest

from aistudio_api.infrastructure.gateway.cookie_rotate import (
    build_cookie_header_string,
    merge_rotated_cookies,
    parse_set_cookie_header,
    rotate_account_cookies,
    rotate_google_cookies_http,
)


def test_parse_set_cookie_header():
    raw = "__Secure-1PSIDTS=sidts-12345; Domain=.google.com; Path=/; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Secure; HttpOnly; SameSite=None"
    parsed = parse_set_cookie_header(raw)
    assert parsed["name"] == "__Secure-1PSIDTS"
    assert parsed["value"] == "sidts-12345"
    assert parsed["domain"] == ".google.com"
    assert parsed["path"] == "/"
    assert parsed["secure"] is True
    assert parsed["httpOnly"] is True
    assert parsed["sameSite"] == "None"


def test_build_cookie_header_string():
    # List format
    cookies_list = [{"name": "SID", "value": "s1"}, {"name": "HSID", "value": "h1"}]
    assert build_cookie_header_string(cookies_list) == "SID=s1; HSID=h1"

    # Dict format
    cookies_dict = {"SID": "s1", "HSID": "h1"}
    assert build_cookie_header_string(cookies_dict) == "SID=s1; HSID=h1"


def test_merge_rotated_cookies():
    old = [
        {"name": "SID", "value": "sid_val"},
        {"name": "__Secure-1PSIDTS", "value": "old_ts"},
    ]
    new = [
        {"name": "__Secure-1PSIDTS", "value": "new_ts"},
        {"name": "SIDCC", "value": "new_sidcc"},
    ]
    merged = merge_rotated_cookies(old, new)
    merged_map = {c["name"]: c["value"] for c in merged}
    assert merged_map["SID"] == "sid_val"
    assert merged_map["__Secure-1PSIDTS"] == "new_ts"
    assert merged_map["SIDCC"] == "new_sidcc"


@pytest.mark.asyncio
async def test_rotate_google_cookies_http_success():
    import httpx
    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.headers = httpx.Headers([
        ("set-cookie", "__Secure-1PSIDTS=new_ts; Domain=.google.com; Path=/; Secure; HttpOnly"),
        ("set-cookie", "SIDCC=new_cc; Domain=.google.com; Path=/; Secure; HttpOnly"),
    ])

    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        ok, updated, err = await rotate_google_cookies_http(
            [
                {"name": "SID", "value": "s1"},
                {"name": "LSID", "value": "l1", "domain": "accounts.google.com"},
            ]
        )
        assert ok is True
        assert err is None
        assert len(updated) == 2
        assert updated[0]["name"] == "__Secure-1PSIDTS"
        assert updated[1]["name"] == "SIDCC"


@pytest.mark.asyncio
async def test_rotate_account_cookies_end_to_end(tmp_path):
    account_dir = tmp_path / "acc_test"
    account_dir.mkdir()
    auth_file = account_dir / "auth.json"
    auth_file.write_text(json.dumps({
        "cookies": [
            {"name": "SID", "value": "test_sid"},
            {"name": "__Secure-1PSIDTS", "value": "old_ts"},
            {"name": "LSID", "value": "test_lsid", "domain": "accounts.google.com"},
        ]
    }), encoding="utf-8")

    meta_file = account_dir / "meta.json"
    meta_file.write_text(json.dumps({"cookie_id": "cid_123"}), encoding="utf-8")

    import httpx
    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.headers = httpx.Headers([
        ("set-cookie", "__Secure-1PSIDTS=refreshed_ts; Domain=.google.com; Path=/; Secure; HttpOnly")
    ])

    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        ok = await rotate_account_cookies(auth_file)
        assert ok is True

    # Verify disk was updated
    updated_data = json.loads(auth_file.read_text(encoding="utf-8"))
    c_map = {c["name"]: c["value"] for c in updated_data["cookies"]}
    assert c_map["__Secure-1PSIDTS"] == "refreshed_ts"
    assert c_map["SID"] == "test_sid"
