"""Unit tests for BrowserSession lifecycle, hot switching, and identity verification."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway.session import BrowserSession


@pytest.mark.asyncio
async def test_session_hot_cookie_switching(tmp_path):
    """Verify single-process cookie hot switching without browser restart."""
    session = BrowserSession(port=9222)
    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    page.cdp = MagicMock()
    page.cdp.send = AsyncMock()
    page.set_cookies = AsyncMock()
    page.goto = AsyncMock()
    page.evaluate = AsyncMock(return_value="already_hooked")
    page.content = AsyncMock(return_value="<html>target@gmail.com</html>")
    page.get_cookies = AsyncMock(return_value=[{"name": "SAPISID", "value": "123"}])

    session._page = page
    session._close_internal = AsyncMock()  # type: ignore[method-assign]

    target_dir = tmp_path / "target_acc"
    target_dir.mkdir()
    target_auth = target_dir / "auth.json"
    target_auth.write_text(
        json.dumps(
            {
                "cookies": [{"name": "SAPISID", "value": "new_cookie"}],
                "origins": [],
            }
        )
    )
    target_meta = target_dir / "meta.json"
    target_meta.write_text(json.dumps({"id": "t1", "email": "target@gmail.com"}))

    await session.switch_auth(str(target_auth))

    # Must NOT call _close_internal (no process kill)
    session._close_internal.assert_not_called()
    page.cdp.send.assert_any_call("Network.clearBrowserCookies")
    page.cdp.send.assert_any_call("Network.clearBrowserCache")
    page.set_cookies.assert_called_once()


@pytest.mark.asyncio
async def test_verify_account_identity_does_not_rmtree(tmp_path):
    """Verify identity verification failure does not delete profile directory."""
    session = BrowserSession(port=9222)
    fake_meta_dir = tmp_path / "acc"
    fake_meta_dir.mkdir()
    fake_auth = fake_meta_dir / "auth.json"
    fake_auth.write_text("{}")
    fake_meta = fake_meta_dir / "meta.json"
    fake_meta.write_text('{"id": "acc_1", "email": "expected@gmail.com"}')
    fake_profile = fake_meta_dir / "profile"
    fake_profile.mkdir()
    (fake_profile / "data.txt").write_text("keep me")

    session._auth_file = str(fake_auth)
    session._profile_dir = str(fake_profile)

    page = MagicMock(spec=CDPPage)
    page.evaluate = AsyncMock(return_value=False)
    page.content = AsyncMock(return_value="<html>other page</html>")
    page.get_cookies = AsyncMock(return_value=[])

    with pytest.raises(RuntimeError, match="页面未登录期望的账号"):
        await session._verify_account_identity(page)

    assert fake_profile.exists()
    assert (fake_profile / "data.txt").exists()


@pytest.mark.asyncio
async def test_session_template_capture_per_model():
    """Verify template capture isolates templates per model."""
    session = BrowserSession(port=9222)
    session._bootstrap_template = {
        "url": "http://bootstrap.com",
        "headers": {},
        "body": '["models/gemini-3.7-flash"]',
    }
    session.ensure_botguard_service = AsyncMock()  # type: ignore[method-assign]
    session._click_run_button = AsyncMock(return_value=True)  # type: ignore[method-assign]
    session._wait_until_idle = AsyncMock()  # type: ignore[method-assign]

    # gemini-3.7-flash uses bootstrap
    tpl1 = await session.capture_template("gemini-3.7-flash")
    assert tpl1["url"] == "http://bootstrap.com"

    mock_page = MagicMock(spec=CDPPage)
    mock_page.evaluate = AsyncMock(return_value="")
    mock_page.fill = AsyncMock()

    def fake_on_request(cb):
        cb(
            {
                "url": "http://pro.com/GenerateContent",
                "headers": {"x-model": "pro"},
                "post_data": '["models/gemini-3.1-pro-preview",' + "x" * 150 + "]",
            }
        )
        return lambda: None

    mock_page.on_request = MagicMock(side_effect=fake_on_request)
    mock_page.on_response = MagicMock(return_value=lambda: None)
    mock_page.wait_for_timeout = AsyncMock()
    session.ensure_botguard_service = AsyncMock(return_value=mock_page)  # type: ignore[method-assign]

    result_tpl = await session.capture_template("gemini-3.1-pro-preview")
    assert result_tpl["url"] == "http://pro.com/GenerateContent"
    hdrs = result_tpl.get("headers")
    assert isinstance(hdrs, dict) and hdrs.get("x-model") == "pro"
