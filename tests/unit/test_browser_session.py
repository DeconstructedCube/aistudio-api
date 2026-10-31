"""Unit tests for BrowserSession in browserless gateway architecture."""

from __future__ import annotations

from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.session import (
    DEFAULT_BOOTSTRAP_TEMPLATE,
    BrowserSession,
)
from aistudio_api.infrastructure.gateway.session_auth import (
    get_aistudio_urls,
    is_login_page_url,
)
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart
from aistudio_api.infrastructure.waa.signer import extract_content_digest


@pytest.mark.asyncio
async def test_browser_session_generate_snapshot():
    session = BrowserSession(port=9222)
    session.set_ready_context(
        SessionContext(
            page=None,
            snap_key="waa_proof",
            auth_user="0",
            auth_file=None,
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
            cookies={"SAPISID": "test_sapisid"},
        )
    )

    mock_signer = MagicMock()
    mock_signer.generate_snapshot = AsyncMock(return_value="!mock_waa_proof_token")
    session._signer = mock_signer

    contents = [
        AistudioContent(role="user", parts=[AistudioPart(text="Hello world")])
    ]
    snapshot = await session.generate_snapshot(contents)
    assert snapshot == "!mock_waa_proof_token"
    mock_signer.generate_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_browser_session_send_hooked_request():
    session = BrowserSession(port=9222)
    session.set_ready_context(
        SessionContext(
            page=None,
            snap_key="waa_proof",
            auth_user="0",
            auth_file=None,
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
            cookies={"SAPISID": "test_sapisid"},
        )
    )

    mock_transport = MagicMock()
    mock_transport.send_hooked_request = AsyncMock(return_value=(200, b'["ok"]'))
    session._transport = mock_transport

    status, body = await session.send_hooked_request(
        body='["models/gemini-3.7-flash"]',
    )
    assert status == 200
    assert body == b'["ok"]'


@pytest.mark.asyncio
async def test_browser_session_send_streaming_request():
    session = BrowserSession(port=9222)
    session.set_ready_context(
        SessionContext(
            page=None,
            snap_key="waa_proof",
            auth_user="0",
            auth_file=None,
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
            cookies={"SAPISID": "test_sapisid"},
        )
    )

    async def fake_streaming(*args, **kwargs):
        yield ("status", 200)
        yield ("chunk", b"hello ")
        yield ("chunk", b"world")

    session._transport.send_streaming_request = fake_streaming

    results = []
    async for tag, payload in session.send_streaming_request(
        body='["models/gemini-3.7-flash"]',
    ):
        results.append((tag, payload))

    assert ("status", 200) in results
    assert ("chunk", b"hello ") in results
    assert ("chunk", b"world") in results


@pytest.mark.asyncio
async def test_generate_snapshot_hash_complex_tools_and_multimodal():
    """验证快照计算规则严格对齐 Google 逆向逻辑：只提取 contents，排除 system_instruction 和 tools。"""
    contents = [
        AistudioContent(
            role="user",
            parts=[
                AistudioPart(text="analyze this image"),
                AistudioPart(inline_data=("image/png", "BASE64_IMAGE_DATA_SAMPLE")),
                AistudioPart(file_id="files/sample_uploaded_file_id"),
            ],
        ),
        AistudioContent(
            role="model",
            parts=[AistudioPart(text="Thinking...")],
        ),
        AistudioContent(
            role="user",
            parts=[AistudioPart(text="continue")],
        ),
    ]

    expected_parts = [
        "analyze this image",
        "BASE64_IMAGE_DATA_SAMPLE",
        "files/sample_uploaded_file_id",
        "Thinking...",
        "continue",
    ]
    expected_prompt = " ".join(expected_parts)
    expected_hash = sha256(expected_prompt.encode("utf-8")).hexdigest()

    prompt, digest = extract_content_digest(contents)
    assert prompt == expected_prompt
    assert digest == expected_hash


@pytest.mark.asyncio
async def test_account_service_activate_account():
    store = MagicMock(spec=AccountStore)
    store.get_account.return_value = AccountMeta(
        id="acc_1", name="Acc 1", email="acc1@test.com", created_at="2026-01-01"
    )
    store.get_auth_path_optional.return_value = "/data/acc1/auth.json"

    session = BrowserSession(port=9222)
    session.switch_auth = AsyncMock()

    service = AccountService(account_store=store)
    acc = await service.activate_account("acc_1", browser_session=session)
    assert acc is not None
    assert acc.id == "acc_1"
    session.switch_auth.assert_called_once_with("/data/acc1/auth.json")


@pytest.mark.asyncio
async def test_capture_service_single_pass_full_payload():
    session = BrowserSession(port=9222)
    session.set_ready_context(
        SessionContext(
            page=None,
            snap_key="waa_proof",
            auth_user="0",
            auth_file=None,
            base_template={
                "url": "https://example.com/generate",
                "headers": {"content-type": "application/json"},
                "body": '["models/gemini-3.7-flash",[[[[null,"p"]],"user"]],null,[null,null,null,128,0.5,0.8,16],"old_snap"]',
            },
        )
    )
    session.generate_snapshot = AsyncMock(return_value="!fresh_snapshot_token")

    service = RequestCaptureService(session=session)
    captured = await service.capture(
        prompt="hello world",
        model="models/gemini-3.7-flash",
    )
    assert captured is not None
    assert captured.snapshot == "!fresh_snapshot_token"
    assert "!fresh_snapshot_token" in captured.body


def test_is_login_page_url():
    assert is_login_page_url("https://accounts.google.com/ServiceLogin")
    assert is_login_page_url("https://accounts.google.com/signin/v2/identifier")
    assert not is_login_page_url("https://aistudio.google.com/prompts/new_chat")


def test_browser_session_temporary_chat_urls():
    urls0 = get_aistudio_urls(auth_user="0")
    assert any("temporary=true" in u for u in urls0)

    urls1 = get_aistudio_urls(auth_user="1")
    assert any("/u/1/" in u for u in urls1)
