import pytest

from aistudio_api.infrastructure.account.cookie_parser import (
    build_google_cookie_list,
    parse_cookie_string,
)
from aistudio_api.infrastructure.account.cookie_refresher import (
    load_cookies_from_string,
)


def test_parse_cookie_string_skips_host_only_cookies_in_storage_state():
    state = parse_cookie_string("__Host-GAPS=abc; SID=sid123")
    cookies = state.get("cookies")
    assert isinstance(cookies, list)
    assert state.get("origins") == []
    assert {cookie["name"] for cookie in cookies if isinstance(cookie, dict)} == {"SID"}
    assert cookies[0]["domain"] == ".google.com"


def test_build_google_cookie_list_uses_url_for_host_targets_when_injecting():
    cookies = build_google_cookie_list(
        [("__Host-GAPS", "abc"), ("LSID", "lsid123"), ("SID", "sid123")],
        allow_url_targets=True,
    )

    by_name = {cookie["name"]: cookie for cookie in cookies}
    assert by_name["__Host-GAPS"]["url"] == "https://accounts.google.com/"
    assert "domain" not in by_name["__Host-GAPS"]
    assert by_name["LSID"]["url"] == "https://accounts.google.com/"
    assert by_name["SID"]["domain"] == ".google.com"


def test_load_cookies_from_string_builds_playwright_safe_cookies(monkeypatch):
    def fake_refresh(_: dict[str, str]) -> dict[str, str]:
        return {
            "__Host-GAPS": "abc",
            "LSID": "lsid123",
            "SID": "sid123",
        }

    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_refresher._refresh_session_cookies",
        fake_refresh,
    )

    cookies = load_cookies_from_string("SID=sid123")
    by_name = {cookie["name"]: cookie for cookie in cookies}

    assert by_name["LSID"]["domain"] == ".google.com"
    assert by_name["SID"]["domain"] == ".google.com"
    assert "__Host-GAPS" not in by_name


def test_load_cookies_from_string_keeps_accounts_cookies_from_raw_string(monkeypatch):
    def fake_refresh(_: dict[str, str]) -> dict[str, str]:
        return {
            "SID": "sid_from_refresh",
            "__Host-GAPS": "gaps_from_refresh",
        }

    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_refresher._refresh_session_cookies",
        fake_refresh,
    )

    cookies = load_cookies_from_string(
        "SID=sid_from_raw; LSID=lsid_raw; __Host-1PLSID=one_raw; ACCOUNT_CHOOSER=chooser_raw"
    )
    by_name = {cookie["name"]: cookie for cookie in cookies}

    assert by_name["SID"]["value"] == "sid_from_refresh"
    assert by_name["LSID"]["domain"] == ".google.com"
    assert by_name["ACCOUNT_CHOOSER"]["domain"] == ".google.com"
    assert "__Host-1PLSID" not in by_name

def test_parse_raw_cookies_json_array_and_netscape():
    from aistudio_api.infrastructure.account.cookie_parser import parse_raw_cookies

    # 1. JSON Array 格式
    json_raw = '[{"name": "SAPISID", "value": "sapisid_val"}, {"name": "SID", "value": "sid_val"}]'
    parsed_json = parse_raw_cookies(json_raw)
    assert parsed_json["SAPISID"] == "sapisid_val"
    assert parsed_json["SID"] == "sid_val"

    # 2. Netscape 7 列 Tab 格式
    netscape_raw = ".google.com\tTRUE\t/\tTRUE\t1788678271\tSAPISID\tnetscape_sapisid\n.google.com\tTRUE\t/\tTRUE\t1788678271\tSID\tnetscape_sid"
    parsed_netscape = parse_raw_cookies(netscape_raw)
    assert parsed_netscape["SAPISID"] == "netscape_sapisid"
    assert parsed_netscape["SID"] == "netscape_sid"


@pytest.mark.asyncio
async def test_model_discovery_parse():
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    raw_mock = [[
        ["models/gemini-3.7-flash", None, "0.1", "Gemini 3.7 Flash", "Flagship flash model", 1000000, 8192, ["generateContent"]],
        ["models/gemini-3.1-flash-image", None, "0.1", "Nano Banana 2", "Image model", 32768, 4096, ["generateContent"]]
    ]]
    parsed = model_discovery._parse_raw_models(raw_mock)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "gemini-3.7-flash"
    assert parsed[0]["category"] == "flash"
    assert parsed[1]["id"] == "gemini-3.1-flash-image"
    assert parsed[1]["category"] == "image"
