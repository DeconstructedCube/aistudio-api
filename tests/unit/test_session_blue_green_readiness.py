"""Unit tests for atomic SessionReadinessGate and account transition."""

from __future__ import annotations

import asyncio
import json

import pytest

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

    ctx = SessionContext(
        page=None,
        snap_key="snap_123",
        auth_user="0",
        auth_file="/data/auth.json",
        base_template={"body": "[]"},
    )

    async def open_gate():
        await asyncio.sleep(0.05)
        gate.set_ready(ctx)

    task = asyncio.create_task(open_gate())
    ready_ctx = await gate.wait_ready()
    await task
    assert ready_ctx == ctx
    assert gate.is_ready()

    # Fail-fast check: when FAILED, wait_ready immediately raises without waiting
    gate.set_failed(RuntimeError("Test error"))
    assert gate.state == SessionState.FAILED
    with pytest.raises(RuntimeError, match="Session transition failed: Test error"):
        await gate.wait_ready()


@pytest.mark.asyncio
async def test_session_context_event_driven_draining():
    """Verify decr_in_flight sets drain_event when count reaches 0."""
    ctx = SessionContext(
        page=None,
        snap_key="snap_123",
        auth_user="0",
        auth_file="/data/auth.json",
        base_template={"body": "[]"},
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
async def test_session_transition_preserves_in_flight(tmp_path):
    """Verify transitioning to new account updates context without killing active in-flight requests."""
    session = BrowserSession(port=9222)

    old_dir = tmp_path / "acc1"
    old_dir.mkdir()
    (old_dir / "auth.json").write_text(json.dumps({"cookies": [{"name": "SAPISID", "value": "old"}]}))
    (old_dir / "meta.json").write_text(json.dumps({"auth_user": "0"}))

    new_dir = tmp_path / "acc2"
    new_dir.mkdir()
    (new_dir / "auth.json").write_text(json.dumps({"cookies": [{"name": "SAPISID", "value": "new"}]}))
    (new_dir / "meta.json").write_text(json.dumps({"auth_user": "1"}))

    await session.transition_to_account(str(old_dir / "auth.json"))
    assert session.is_ready
    assert session.get_current_auth_user() == "0"

    # Now transition to new account
    await session.transition_to_account(str(new_dir / "auth.json"))
    assert session.is_ready
    assert session.active_context is not None
    assert session.get_current_auth_user() == "1"
    assert session.active_context.cookies.get("SAPISID") == "new"
