"""Unit tests for BrowserSession lifecycle, hot switching, and identity verification."""

from __future__ import annotations

import json
import time
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

    assert await session._verify_account_identity(page) is False

    assert fake_profile.exists()
    assert (fake_profile / "data.txt").exists()


@pytest.mark.asyncio
async def test_session_template_capture_universal_bootstrap():
    """Verify template capture universally uses bootstrap template to prevent DOM GC timeout."""
    session = BrowserSession(port=9222)
    session._bootstrap_template = {
        "url": "http://bootstrap.com",
        "headers": {},
        "body": '["models/gemini-3.7-flash"]',
    }

    mock_page = MagicMock(spec=CDPPage)
    session.ensure_botguard_service = AsyncMock(return_value=mock_page)  # type: ignore[method-assign]

    # 3.7-flash uses bootstrap
    tpl1 = await session.capture_template("gemini-3.7-flash")
    assert tpl1["url"] == "http://bootstrap.com"

    # Other models ALSO use bootstrap now, saving 30 seconds
    tpl2 = await session.capture_template("gemini-3.1-pro-preview")
    assert tpl2["url"] == "http://bootstrap.com"


@pytest.mark.asyncio
async def test_session_check_idle_timeout_releases_browser(monkeypatch):
    """Verify check_idle_timeout releases browser process when idle beyond threshold."""
    from aistudio_api.config import settings

    monkeypatch.setattr(settings, "browser_idle_timeout", 5)

    session = BrowserSession(port=9222)
    session._proc = MagicMock()
    session._in_flight = 0
    session._last_activity_time = 0.0  # 很久以前
    session._close_internal = AsyncMock()

    released = await session.check_idle_timeout()
    assert released is True
    session._close_internal.assert_called_once()


@pytest.mark.asyncio
async def test_session_capture_template_breaks_on_dead_proc():
    """Verify capture_template_flow immediately raises on dead process without 30s delay."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = False
    session._proc = mock_proc

    mock_page = MagicMock(spec=CDPPage)
    mock_page.is_closed.return_value = False
    mock_page.evaluate = AsyncMock(return_value="")
    mock_page.fill = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    session.ensure_botguard_service = AsyncMock(return_value=mock_page)
    session._click_run_button = AsyncMock(return_value=True)
    with pytest.raises(
        RuntimeError, match="Browser process died during template capture"
    ):
        await session.capture_template("models/gemini-custom-uncached")


@pytest.mark.asyncio
async def test_ensure_browser_cdp_refreshes_activity_timestamp(monkeypatch):
    """Verify cold-starting the browser refreshes the idle activity timestamp.

    回归：_last_activity_time 原先只在 request_scope() 内更新。长时间空闲后由
    切号 / 预热 / BotGuard 刷新等非请求路径冷启动浏览器时，时间戳仍是上一个请求
    留下的旧值，每 10s 跑一次的空闲回收器会把刚拉起的进程当成"空闲超时"直接
    回收；而失败路径永远走不到 request_scope()，时间戳无法自愈更新，服务就此
    瘫痪直到手动重启。
    """
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway import session as session_module

    session = BrowserSession(port=9222)
    session._auth_file = None
    session._in_flight = 0
    session._last_activity_time = time.time() - 115_285.0  # 上一个请求在 32 小时前

    fake_page = MagicMock(spec=CDPPage)
    fake_page.cdp = MagicMock()
    fake_page.cdp.send = AsyncMock()
    fake_client = MagicMock()
    fake_client.connect_page = AsyncMock(return_value=fake_page)

    monkeypatch.setattr(
        session_module, "launch_chromium_process", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        session_module, "CDPClient", MagicMock(return_value=fake_client)
    )
    session._goto_aistudio = AsyncMock()  # type: ignore[method-assign]
    session._install_hooks = AsyncMock()  # type: ignore[method-assign]

    await session._ensure_browser_cdp()

    # 启动过程必须刷新活跃时间戳，否则回收器会误判为"长时间空闲"
    assert time.time() - session._last_activity_time < 5.0

    monkeypatch.setattr(settings, "browser_idle_timeout", 1800)
    session._close_internal = AsyncMock()  # type: ignore[method-assign]
    assert await session.check_idle_timeout() is False
    session._close_internal.assert_not_called()
