"""Unit tests for BrowserSession lifecycle, hot switching, and identity verification."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_auth import verify_account_identity
from aistudio_api.infrastructure.gateway.session_state import SessionContext


@pytest.mark.asyncio
async def test_session_hot_cookie_switching(tmp_path):
    """Verify single-process cookie hot switching without browser restart."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc

    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.connect_page = AsyncMock(return_value=page)
    session._cdp_client = mock_cdp

    session._close_internal = AsyncMock()

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

    with (
        patch(
            "aistudio_api.infrastructure.gateway.session.load_cookies_to_page",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.navigate_to_aistudio",
            AsyncMock(),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.bootstrap_botguard_and_template",
            AsyncMock(return_value=("snap_123", {"url": "http://test", "body": "[]"})),
        ),
    ):
        await session.switch_auth(str(target_auth))

    # Must NOT call _close_internal (no process kill)
    session._close_internal.assert_not_called()
    assert session.is_ready
    assert session.active_context is not None
    assert session.active_context.page == page


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

    assert await verify_account_identity(page, str(fake_auth)) is False

    assert fake_profile.exists()
    assert (fake_profile / "data.txt").exists()


@pytest.mark.asyncio
async def test_session_template_capture_universal_bootstrap():
    """Verify template capture universally uses bootstrap template to prevent DOM GC timeout."""
    session = BrowserSession(port=9222)
    mock_page = MagicMock(spec=CDPPage)
    session.set_ready_context(
        SessionContext(
            page=mock_page,
            snap_key="snap_key",
            auth_user="0",
            auth_file=None,
            base_template={
                "url": "http://bootstrap.com",
                "headers": {},
                "body": '["models/gemini-3.7-flash"]',
            },
        )
    )

    # 3.7-flash uses bootstrap
    tpl1 = await session.capture_template("gemini-3.7-flash")
    assert tpl1["url"] == "http://bootstrap.com"

    # Other models ALSO use bootstrap now, saving 30 seconds
    tpl2 = await session.capture_template("gemini-3.1-pro-preview")
    assert tpl2["url"] == "http://bootstrap.com"


@pytest.mark.asyncio
async def test_session_capture_template_breaks_on_dead_proc():
    """Verify capture_template_flow immediately raises on dead process without 30s delay."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = False
    session._proc = mock_proc

    session._close_internal = AsyncMock()
    with pytest.raises(
        RuntimeError, match="Browser process died during template capture"
    ):
        await session.capture_template("models/gemini-custom-uncached")
