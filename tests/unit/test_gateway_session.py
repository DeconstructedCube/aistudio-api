"""Unit tests for GatewaySession and session state management in browserless architecture."""

from __future__ import annotations

import json
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.session import (
    GatewaySession,
    SessionContext,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart
from aistudio_api.infrastructure.waa.signer import extract_content_digest


@pytest.mark.asyncio
async def test_gateway_session_generate_snapshot():
    session = GatewaySession()
    session.set_ready_context(
        SessionContext(
            auth_user="0",
            auth_file=None,
            cookies={"SAPISID": "test_sapisid"},
        )
    )

    mock_signer = MagicMock()
    mock_signer.generate_snapshot = AsyncMock(return_value="!mock_waa_proof_token")
    session._signer = mock_signer

    contents = [AistudioContent(role="user", parts=[AistudioPart(text="Hello world")])]
    snapshot = await session.generate_snapshot(contents)
    assert snapshot == "!mock_waa_proof_token"
    mock_signer.generate_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_gateway_session_send_request():
    session = GatewaySession()
    session.set_ready_context(
        SessionContext(
            auth_user="0",
            auth_file=None,
            cookies={"SAPISID": "test_sapisid"},
        )
    )

    mock_transport = MagicMock()
    mock_transport.send_request = AsyncMock(return_value=(200, b'["ok"]'))
    session._transport = mock_transport

    status, body = await session.send_request(
        body='["models/gemini-3.7-flash"]',
    )
    assert status == 200
    assert body == b'["ok"]'


@pytest.mark.asyncio
async def test_gateway_session_send_streaming_request():
    session = GatewaySession()
    session.set_ready_context(
        SessionContext(
            auth_user="0",
            auth_file=None,
            cookies={"SAPISID": "test_sapisid"},
        )
    )

    async def fake_streaming(*_args, **_kwargs):
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

    session = GatewaySession()
    session.switch_auth = AsyncMock()

    service = AccountService(account_store=store)
    acc = await service.activate_account("acc_1", session=session)
    assert acc is not None
    assert acc.id == "acc_1"
    session.switch_auth.assert_called_once_with("/data/acc1/auth.json")


@pytest.mark.asyncio
async def test_prepare_request_single_pass_full_payload():
    session = GatewaySession()
    session.set_ready_context(
        SessionContext(
            auth_user="0",
            auth_file=None,
            cookies={"SAPISID": "test_sapisid"},
        )
    )
    session.generate_snapshot = AsyncMock(return_value="!fresh_snapshot_token")

    client = AIStudioClient()
    client._session = session
    prepared = await client.prepare_request(
        prompt="hello world",
        model="models/gemini-3.7-flash",
    )
    assert prepared is not None
    assert prepared.snapshot == "!fresh_snapshot_token"
    assert "!fresh_snapshot_token" in prepared.body


@pytest.mark.asyncio
async def test_session_transition_preserves_in_flight(tmp_path):
    """Verify transitioning to new account updates context cleanly in memory."""
    session = GatewaySession()

    old_dir = tmp_path / "acc1"
    old_dir.mkdir()
    (old_dir / "auth.json").write_text(
        json.dumps({"cookies": [{"name": "SAPISID", "value": "old"}]})
    )
    (old_dir / "meta.json").write_text(json.dumps({"auth_user": "0"}))

    new_dir = tmp_path / "acc2"
    new_dir.mkdir()
    (new_dir / "auth.json").write_text(
        json.dumps({"cookies": [{"name": "SAPISID", "value": "new"}]})
    )
    (new_dir / "meta.json").write_text(json.dumps({"auth_user": "1"}))

    await session.transition_to_account(str(old_dir / "auth.json"))
    assert session.is_ready
    assert session.get_current_auth_user() == "0"

    await session.transition_to_account(str(new_dir / "auth.json"))
    assert session.is_ready
    assert session.active_context is not None
    assert session.get_current_auth_user() == "1"
    assert session.active_context.cookies.get("SAPISID") == "new"


@pytest.mark.asyncio
async def test_streaming_gateway_raises_transport_error_instead_of_http_0():
    """Regression test: transport error events or missing HTTP status must raise TransportError, never RequestError(0) ('HTTP 0')."""
    from aistudio_api.domain.errors import TransportError
    from aistudio_api.infrastructure.gateway.client import PreparedRequest
    from aistudio_api.infrastructure.gateway.streaming import StreamingGateway

    session = GatewaySession()

    async def fake_error_stream(**_kwargs):
        yield ("error", "ReadTimeout")

    session.send_streaming_request = fake_error_stream
    gateway = StreamingGateway(session=session)
    prepared = PreparedRequest(
        url="https://example.com",
        headers={},
        body="[]",
        model="models/gemini-4-argon",
        snapshot="!snap",
    )

    with pytest.raises(TransportError, match="ReadTimeout"):
        async for _ in gateway.stream_chat(
            request=prepared, model="models/gemini-4-argon"
        ):
            pass
