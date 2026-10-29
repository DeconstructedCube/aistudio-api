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

    raw_mock = [
        [
            [
                "models/gemini-3.7-flash",
                None,
                "0.1",
                "Gemini 3.7 Flash",
                "Flagship flash model",
                1000000,
                8192,
                ["generateContent"],
            ],
            [
                "models/gemini-3.1-flash-image",
                None,
                "0.1",
                "Nano Banana 2",
                "Image model",
                32768,
                4096,
                ["generateContent"],
            ],
        ]
    ]
    parsed = model_discovery._parse_raw_models(raw_mock)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "gemini-3.7-flash"
    assert parsed[0]["category"] == "flash"
    assert parsed[1]["id"] == "gemini-3.1-flash-image"
    assert parsed[1]["category"] == "image"


def test_parse_cookie_string_discards_transient_and_telemetry_cookies():
    raw = (
        "SID=sid_val; SAPISID=sapisid_val; __Secure-1PSID=psid_val; "
        "_ga=GA1.2.123; NID=511=nid_val; SIDCC=sidcc_val; "
        "OSID=stale_osid; __Secure-OSID=stale_sec_osid"
    )
    state = parse_cookie_string(raw, discard_transient=True)
    raw_cookies = state.get("cookies")
    assert isinstance(raw_cookies, list)
    names = {c["name"] for c in raw_cookies if isinstance(c, dict)}
    # Core credentials must be kept
    assert "SID" in names
    assert "SAPISID" in names
    assert "__Secure-1PSID" in names

    # Stale/transient/tracking cookies must be discarded
    assert "_ga" not in names
    assert "NID" not in names
    assert "SIDCC" not in names
    assert "OSID" not in names
    assert "__Secure-OSID" not in names


def test_domain_overrides_scopes_osid_strictly_to_aistudio():
    from aistudio_api.infrastructure.account.cookie_parser import _DOMAIN_OVERRIDES

    assert _DOMAIN_OVERRIDES["OSID"] == ["aistudio.google.com"]
    assert _DOMAIN_OVERRIDES["__Secure-OSID"] == ["aistudio.google.com"]
    assert ".google.com" not in _DOMAIN_OVERRIDES["OSID"]
    assert ".google.com" not in _DOMAIN_OVERRIDES["__Secure-OSID"]


@pytest.mark.asyncio
async def test_import_bundle_flow(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from aistudio_api.api import routes_accounts
    from aistudio_api.api.routes_accounts import ImportBundleRequest, import_bundle
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_store import AccountStore

    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value="Free")
    )
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_email",
        AsyncMock(return_value="mock@example.com"),
    )
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    svc = AccountService(store)

    bundle_content = """{
      "version": 1,
      "accounts": [
        {
          "name": "Work Profile",
          "email": "work@example.com",
          "cookies": "SID=work_sid; SAPISID=work_sapisid",
          "auth_user": "0"
        },
        {
          "name": "Personal Profile",
          "email": "personal@example.com",
          "cookies": "SID=pers_sid; SAPISID=pers_sapisid",
          "auth_user": "1"
        }
      ]
    }"""

    req = ImportBundleRequest(content=bundle_content)
    res = await import_bundle(req, account_service=svc)

    assert res.imported_count == 2
    assert len(res.accounts) == 2
    assert res.accounts[0].name == "Work Profile"
    assert res.accounts[0].email == "work@example.com"
    assert res.accounts[1].name == "Personal Profile"
    assert res.accounts[1].auth_user == "1"

    # 验证磁盘持久化有效
    persisted = svc.list_accounts()
    assert len(persisted) == 2
    assert svc.get_active_account() is not None


def test_mask_email():
    """测试邮箱地址脱敏处理。"""
    from aistudio_api.infrastructure.utils.common import mask_email

    assert mask_email("user123@gmail.com") == "u***3@gmail.com"
    assert mask_email("ab@gmail.com") == "a***@gmail.com"
    assert mask_email("a@gmail.com") == "a***@gmail.com"
    assert mask_email(None) == ""
    assert mask_email("not_an_email") == "not_an_email"


def test_update_account_email(tmp_path):
    """测试自动识别并更新账号邮箱。"""
    from aistudio_api.infrastructure.account.account_store import AccountStore

    store = AccountStore(accounts_dir=tmp_path / "accounts_test")
    acc = store.save_account(
        name="Test",
        email=None,
        storage_state={"cookies": []},
    )
    assert acc.email is None

    updated = store.update_account_email(acc.id, "real_user@gmail.com")
    assert updated is True

    reloaded = store.get_account(acc.id)
    assert reloaded is not None
    assert reloaded.email == "real_user@gmail.com"

    # 重复更新相同邮箱返回 False
    assert store.update_account_email(acc.id, "real_user@gmail.com") is False


def test_parse_benefit_tier_and_update_account_tier(tmp_path):
    """测试 GetAiStudioBenefitTier 响应解析、请求头映射及账号等级持久化。"""
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.account.cookie_parser import (
        get_tier_header_value,
        parse_benefit_tier_response,
    )

    assert parse_benefit_tier_response("[]") == "Free"
    assert parse_benefit_tier_response("[null]") == "Free"
    assert parse_benefit_tier_response("[0]") == "Free"
    assert parse_benefit_tier_response(")]}'\n[1]") == "Pro"
    assert parse_benefit_tier_response([2]) == "Ultra"
    assert parse_benefit_tier_response([3]) == "Plus"

    assert get_tier_header_value("Free") is None
    assert get_tier_header_value("Pro") == "TIER1"
    assert get_tier_header_value("Ultra") == "TIER2"
    assert get_tier_header_value("Plus") == "TIER0"
    assert get_tier_header_value("Ultra_20X") == "TIER2"
    assert get_tier_header_value("ultra_20x") == "TIER2"

    store = AccountStore(accounts_dir=tmp_path / "accounts_tier_test")
    acc = store.save_account(
        name="Tier Test",
        email="tier@gmail.com",
        storage_state={"cookies": []},
    )
    assert acc.tier == "Free"
    assert store.update_account_tier(acc.id, "Pro") is True
    reloaded = store.get_account(acc.id)
    assert reloaded is not None
    assert reloaded.tier == "Pro"
    assert store.update_account_tier(acc.id, "Pro") is False
    assert store.update_account_tier(acc.id, "Ultra_20X") is True
    reloaded_u20 = store.get_account(acc.id)
    assert reloaded_u20 is not None
    assert reloaded_u20.tier == "Ultra_20X"


def test_parse_emails_from_ogs_html_and_meta_reconciliation(tmp_path):
    """测试 Google OGS/AI Studio HTML 邮箱提取以及 meta.json 与 registry.json 自动对齐。"""
    import json

    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.account.cookie_parser import (
        parse_emails_from_ogs_html,
    )

    sample_html = (
        '{"oPEP7c":"user0@gmail.com"} '
        '<a data-au="1" data-email="user1@gmail.com"></a> '
        '<a data-email="user2@gmail.com" data-au="2"></a>'
    )
    emails = parse_emails_from_ogs_html(sample_html, "0")
    assert emails == {
        "0": "user0@gmail.com",
        "1": "user1@gmail.com",
        "2": "user2@gmail.com",
    }

    # 验证当 meta.json 被外部/浏览器更新了 email 与 tier，而 registry.json 仍为旧值时，加载自动对齐
    acc_dir = tmp_path / "accounts_reconcile"
    store = AccountStore(accounts_dir=acc_dir)
    acc = store.save_account(
        name="Reconcile Test",
        email=None,
        storage_state={"cookies": []},
    )
    meta_path = acc_dir / acc.id / "meta.json"
    meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_data["email"] = "reconciled@gmail.com"
    meta_data["tier"] = "Pro"
    meta_path.write_text(json.dumps(meta_data), encoding="utf-8")

    # 模拟重新加载注册表
    store._registry = None
    reloaded = store.get_account(acc.id)
    assert reloaded is not None
    assert reloaded.email == "reconciled@gmail.com"
    assert reloaded.tier == "Pro"


@pytest.mark.asyncio
async def test_delete_cookie_group_migrates_active_account(tmp_path):
    """测试批量删除包含当前活跃账号的 Cookie 组时，正确切换活跃账号与浏览器会话。"""
    from unittest.mock import AsyncMock, MagicMock

    from aistudio_api.api.routes_accounts import delete_cookie_group
    from aistudio_api.api.state import RuntimeState
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_store import AccountStore

    store = AccountStore(accounts_dir=tmp_path / "accounts_grp_test")
    svc = AccountService(store)

    # 创建属于 group1 的两个子账号 (u/0 和 u/1)
    acc1 = svc.save_account_from_cookies(
        name="Acc1",
        email="acc1@gmail.com",
        storage_state={"cookies": []},
        auth_user="0",
        cookie_id="group_test_123",
    )
    svc.save_account_from_cookies(
        name="Acc2",
        email="acc2@gmail.com",
        storage_state={"cookies": []},
        auth_user="1",
        cookie_id="group_test_123",
    )
    # 创建属于 group2 的备用账号
    acc3 = svc.save_account_from_cookies(
        name="Acc3",
        email="acc3@gmail.com",
        storage_state={"cookies": []},
        auth_user="0",
        cookie_id="group_test_456",
    )

    svc.set_active_account(acc1.id)
    active_before = svc.get_active_account()
    assert active_before is not None and active_before.id == acc1.id

    mock_session = MagicMock()
    mock_session.switch_auth = AsyncMock()
    mock_client = MagicMock()
    mock_client._session = mock_session

    state = RuntimeState(
        client=mock_client,
        account_service=svc,
    )

    # 删除 group_test_123 (包含当前 active 的 acc1)
    res = await delete_cookie_group(
        cookie_id="group_test_123",
        account_service=svc,
        runtime_state=state,
    )
    assert res == {"deleted": 2}

    # 活跃账号应当自动切换至剩余账号 acc3
    new_active = svc.get_active_account()
    assert new_active is not None
    assert new_active.id == acc3.id
    # 且触发了 browser_session.switch_auth
    assert mock_session.switch_auth.called


@pytest.mark.asyncio
async def test_probe_import_activates_first_account_and_registers_in_rotator(
    monkeypatch,
):
    """Verify probe_and_import registers accounts in rotator and activates first account when registry was empty (PR #1 & Issue #7)."""
    from unittest.mock import AsyncMock, MagicMock

    from aistudio_api.api.routes_accounts import (
        ProbeAndImportRequest,
        probe_and_import,
    )
    from aistudio_api.infrastructure.account import cookie_parser
    from aistudio_api.infrastructure.account.account_store import AccountMeta

    imported = AccountMeta(
        id="acc_new",
        name="New u/0",
        email="new@gmail.com",
        created_at="2026-09-27T00:00:00+00:00",
        auth_user="0",
        cookie_id="cookie_new",
    )
    account_service = MagicMock()
    account_service.get_active_account.return_value = None
    account_service.batch_import_accounts.return_value = [imported]
    account_service.activate_account = AsyncMock(return_value=imported)

    browser_session = MagicMock()
    rotator = MagicMock()
    runtime_state = MagicMock()
    runtime_state.client._session = browser_session
    runtime_state.rotator = rotator

    monkeypatch.setattr(
        cookie_parser,
        "probe_google_accounts_infinite",
        AsyncMock(return_value=[{"auth_user": "0", "status_code": 200}]),
    )

    response = await probe_and_import(
        ProbeAndImportRequest(cookies="SAPISID=test-value"),
        account_service=account_service,
        runtime_state=runtime_state,
    )

    assert response.imported_count == 1
    rotator.add_account.assert_called_once_with("acc_new")
    rotator.clear_cooldown.assert_called_once_with("acc_new")
    account_service.activate_account.assert_awaited_once_with(
        "acc_new", browser_session
    )
