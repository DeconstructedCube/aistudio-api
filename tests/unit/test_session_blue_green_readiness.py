"""Unit tests for atomic SessionReadinessGate and Blue-Green tab transition."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import (
    SessionContext,
    SessionReadinessGate,
    SessionState,
)


@pytest.mark.asyncio
async def test_session_readiness_gate_state_machine():
    """Verify gate transitions cleanly: UNINITIALIZED -> PREPARING -> READY -> FAILED."""
    gate = SessionReadinessGate()
    assert gate.state == SessionState.UNINITIALIZED
    assert not gate.is_ready()

    # When PREPARING, wait_ready blocks until ready
    gate.set_preparing()
    assert gate.state == SessionState.PREPARING

    mock_page = MagicMock(spec=CDPPage)
    mock_page.is_closed.return_value = False
    ctx = SessionContext(
        page=mock_page,
        snap_key="snap_123",
        auth_user="0",
        auth_file="/data/auth.json",
        base_template={"body": "[]"},
    )

    async def open_gate():
        await asyncio.sleep(0.05)
        gate.set_ready(ctx)

    task = asyncio.create_task(open_gate())
    ready_ctx = await gate.wait_ready(timeout_s=2.0)
    await task
    assert ready_ctx == ctx
    assert gate.is_ready()

    # Fail-fast check: when FAILED, wait_ready immediately raises without waiting
    gate.set_failed(RuntimeError("Test error"))
    assert gate.state == SessionState.FAILED
    with pytest.raises(RuntimeError, match="Session transition failed: Test error"):
        await gate.wait_ready(timeout_s=1.0)


@pytest.mark.asyncio
async def test_session_context_event_driven_draining():
    """Verify decr_in_flight sets drain_event when count reaches 0."""
    mock_page = MagicMock(spec=CDPPage)
    mock_page.is_closed.return_value = False
    ctx = SessionContext(
        page=mock_page,
        snap_key="snap_123",
        auth_user="0",
        auth_file=None,
        base_template={},
        in_flight=2,
    )
    assert not ctx.drain_event.is_set()

    ctx.decr_in_flight()
    assert ctx.in_flight == 1
    assert not ctx.drain_event.is_set()

    ctx.decr_in_flight()
    assert ctx.in_flight == 0
    assert ctx.drain_event.is_set()


@pytest.mark.asyncio
async def test_session_blue_green_transition_preserves_inflight():
    """Verify blue-green transition creates a new page when in_flight > 0 without killing running streams."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc

    old_page = MagicMock(spec=CDPPage)
    old_page.is_closed.return_value = False
    old_page.target_id = "page_tab_1"
    old_page.url = "https://aistudio.google.com/u/0/prompts/new_chat"

    new_page = MagicMock(spec=CDPPage)
    new_page.is_closed.return_value = False
    new_page.target_id = "page_tab_2"
    new_page.url = "https://aistudio.google.com/u/1/prompts/new_chat"

    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.create_new_page = AsyncMock(return_value=new_page)
    mock_cdp.close_target = AsyncMock()
    session._cdp_client = mock_cdp

    # Setup old active context with in_flight = 1 (a request is actively reading)
    old_ctx = SessionContext(
        page=old_page,
        snap_key="snap_old",
        auth_user="0",
        auth_file="/data/acc1.json",
        base_template={"body": '["models/gemini-3.7-flash"]'},
        in_flight=1,
    )
    session._gate.set_ready(old_ctx)
    session._in_flight = 1

    # Mock bootstrap functions
    from unittest.mock import patch

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
            AsyncMock(
                return_value=("snap_new", {"body": '["models/gemini-3.8-flash"]'})
            ),
        ),
    ):
        new_ctx = await session.transition_to_account(
            "/data/acc2.json", force_refresh=True
        )

    # 1. New page is created and becomes active context
    assert new_ctx.page == new_page
    assert session.active_context == new_ctx
    assert session._gate.is_ready()

    # 2. Old page was NOT aborted or closed immediately because in_flight was 1!
    old_page.close.assert_not_called()
    assert old_ctx in session._retiring_contexts

    # 3. Old stream finishes and decrements in_flight
    old_ctx.decr_in_flight()
    await asyncio.sleep(0.05)

    # Now old page is cleanly closed and closed on CDP
    old_page.close.assert_called_once()
    mock_cdp.close_target.assert_called_once_with("page_tab_1")
    assert old_ctx not in session._retiring_contexts


@pytest.mark.asyncio
async def test_session_blue_green_transition_closes_new_tab_on_failure():
    """Verify failed transition closes the newly created tab to prevent orphan tab leaks."""
    from unittest.mock import patch

    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc

    old_page = MagicMock(spec=CDPPage)
    old_page.is_closed.return_value = False
    old_page.target_id = "page_tab_1"

    leaked_page = MagicMock(spec=CDPPage)
    leaked_page.is_closed.return_value = False
    leaked_page.target_id = "page_tab_failed_2"
    leaked_page.close = AsyncMock()

    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.create_new_page = AsyncMock(return_value=leaked_page)
    mock_cdp.close_target = AsyncMock()
    session._cdp_client = mock_cdp

    old_ctx = SessionContext(
        page=old_page,
        snap_key="snap_old",
        auth_user="0",
        auth_file="/data/acc1.json",
        base_template={"body": '["models/gemini-3.7-flash"]'},
    )
    session._gate.set_ready(old_ctx)

    with (
        patch(
            "aistudio_api.infrastructure.gateway.session.load_cookies_to_page",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.navigate_to_aistudio",
            AsyncMock(side_effect=RuntimeError("Navigation or BotGuard failed")),
        ),
        pytest.raises(RuntimeError, match="Navigation or BotGuard failed"),
    ):
        await session.transition_to_account("/data/acc_broken.json", force_refresh=True)

    leaked_page.close.assert_awaited_once()
    mock_cdp.close_target.assert_awaited_once_with("page_tab_failed_2")
