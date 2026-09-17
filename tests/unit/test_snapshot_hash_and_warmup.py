"""Tests for BotGuard snapshot hash calculation and browser warmup integration."""

from __future__ import annotations

from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart


@pytest.mark.asyncio
async def test_generate_snapshot_hash_complex_tools_and_multimodal():
    """Verify that hash calculation accurately extracts parts according to _.Nv(request)."""
    session = BrowserSession(port=9222)
    mock_page = MagicMock(spec=CDPPage)
    mock_page.url = "https://aistudio.google.com/prompts/new_chat"
    mock_page.is_closed = MagicMock(return_value=False)

    passed_args = []

    async def fake_evaluate(expr, args=None, *a, **kw):
        if "INSTALL_HOOKS" in expr or "window.__bg_hooked" in expr or "return 'already_hooked'" in expr:
            return "already_hooked"
        if "!window.__bg_service" in expr:
            return True
        if "Promise.resolve(result)" in expr:
            passed_args.append(args)
            return "!snapshot_token_ok"
        return None

    mock_page.evaluate.side_effect = fake_evaluate
    session._page = mock_page
    session._snap_key = "snap_fn"

    # Complex multi-turn contents with text, inline data, file_id, function_call, and function_response
    contents = [
        AistudioContent(
            role="user",
            parts=[
                AistudioPart(text="What is the weather in Tokyo?"),
                AistudioPart(inline_data=("image/png", "iVBORw0KGgoAAAANSUhEUg==")),
            ],
        ),
        AistudioContent(
            role="model",
            parts=[
                AistudioPart(
                    function_call=("get_weather", {"city": "Tokyo"}, "call_123"),
                    thought=True,
                ),
            ],
        ),
        AistudioContent(
            role="user",
            parts=[
                AistudioPart(
                    function_response=("get_weather", {"temp": "22C"}, "call_123")
                ),
                AistudioPart(file_id="files/sample_data_file"),
            ],
        ),
    ]

    expected_parts = [
        "What is the weather in Tokyo?",
        "iVBORw0KGgoAAAANSUhEUg==",
        "",  # function_call maps to ""
        "",  # function_response maps to ""
        "files/sample_data_file",
    ]
    expected_hash = sha256(" ".join(expected_parts).encode("utf-8")).hexdigest()

    snapshot = await session.generate_snapshot(contents)
    assert snapshot == "!snapshot_token_ok"
    assert passed_args == [expected_hash]


@pytest.mark.asyncio
async def test_account_service_activate_account_warms_up_browser():
    """Verify activate_account invokes ensure_botguard_service for instant readiness."""
    mock_store = MagicMock(spec=AccountStore)
    mock_store.get_account.return_value = AccountMeta(
        id="acc_1",
        name="Test Account",
        email="test@example.com",
        created_at="2026-09-01T00:00:00Z",
        last_used="2026-09-01T00:00:00Z",
        auth_user="0",
    )
    mock_store.get_auth_path_optional.return_value = "/data/accounts/acc_1/auth.json"

    mock_session = MagicMock(spec=BrowserSession)
    mock_session.switch_auth = AsyncMock()
    mock_session.ensure_botguard_service = AsyncMock()

    cache = SnapshotCache()
    service = AccountService(mock_store)

    result = await service.activate_account(
        account_id="acc_1",
        browser_session=mock_session,
        snapshot_cache=cache,
    )

    assert result is not None
    assert result.id == "acc_1"
    mock_session.switch_auth.assert_called_once_with("/data/accounts/acc_1/auth.json")
    mock_session.ensure_botguard_service.assert_called_once()


@pytest.mark.asyncio
async def test_capture_service_single_pass_full_payload():
    """Verify capture service builds full modified_body with system instruction and tools in one pass."""
    mock_session = MagicMock(spec=BrowserSession)
    mock_session.capture_template = AsyncMock(
        return_value={
            "url": "https://example.com/generate",
            "headers": {"content-type": "application/json"},
            "body": '["models/gemini-3.7-flash",[[[[null,"hi"]],"user"]],null,[null,null,null,128],"!orig"]',
        }
    )
    mock_session.generate_snapshot = AsyncMock(return_value="!fresh_snapshot_token")

    cache = SnapshotCache()
    capture_svc = RequestCaptureService(mock_session, cache)

    contents = [
        AistudioContent(
            role="user",
            parts=[AistudioPart(text="Hello from user")],
        )
    ]
    sys_inst = AistudioContent(
        role="user",
        parts=[AistudioPart(text="You are a helpful assistant")],
    )
    tools = [[[]]]  # code execution tool

    captured = await capture_svc.capture(
        prompt="Hello from user",
        model="models/gemini-3.7-flash",
        contents=contents,
        system_instruction_content=sys_inst,
        tools=tools,
        max_tokens=256,
        temperature=0.5,
    )

    assert captured is not None
    assert captured.snapshot == "!fresh_snapshot_token"
    assert "You are a helpful assistant" in captured.body
    assert "models/gemini-3.7-flash" in captured.body
