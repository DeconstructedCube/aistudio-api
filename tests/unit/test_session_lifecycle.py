"""Unit tests for BrowserSession lifecycle and hot switching in browserless architecture."""

from __future__ import annotations

import json

import pytest

from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext


@pytest.mark.asyncio
async def test_session_hot_cookie_switching(tmp_path):
    """Verify in-memory account hot switching without external processes."""
    session = BrowserSession(port=9222)

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
    target_meta.write_text(json.dumps({"id": "t1", "email": "target@gmail.com", "auth_user": "1"}))

    await session.switch_auth(str(target_auth))

    assert session.is_ready
    assert session.active_context is not None
    assert session.active_context.auth_file == str(target_auth)
    assert session.active_context.auth_user == "1"
    assert session.active_context.cookies.get("SAPISID") == "new_cookie"


@pytest.mark.asyncio
async def test_session_template_capture_universal():
    """Verify template capture universally formats target model in wire body."""
    session = BrowserSession(port=9222)
    session.set_ready_context(
        SessionContext(
            page=None,
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

    tpl1 = await session.capture_template("gemini-3.7-flash")
    assert tpl1["url"] == "http://bootstrap.com"
    assert "models/gemini-3.7-flash" in str(tpl1["body"])

    tpl2 = await session.capture_template("gemini-3.1-pro-preview")
    assert tpl2["url"] == "http://bootstrap.com"
    assert "models/gemini-3.1-pro-preview" in str(tpl2["body"])
