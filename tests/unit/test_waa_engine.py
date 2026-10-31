"""Unit tests for WAA engine proof signing client (aistudio-pg-bridge / spinsnow)."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import httpx
import pytest

from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart
from aistudio_api.infrastructure.waa.signer import (
    RemoteProofSigner,
    WaaSigner,
    extract_content_digest,
)


def test_extract_content_digest_exact_alignment():
    """Verify content digest only hashes message parts joined by single spaces."""
    contents = [
        AistudioContent(role="user", parts=[AistudioPart(text="hello")]),
        AistudioContent(role="model", parts=[AistudioPart(text="world")]),
    ]
    prompt, digest = extract_content_digest(contents)
    assert prompt == "hello world"
    assert digest == hashlib.sha256(b"hello world").hexdigest()


@pytest.mark.asyncio
async def test_remote_proof_signer_success():
    """Verify RemoteProofSigner sends payload and parses proof token."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["cookies"] == "SAPISID=test_cookie"
        assert data["authUser"] == 0
        assert data["prompt"] == "hello"
        assert data["wireContents"] == [[[[None, "hello"]], "user"]]
        return httpx.Response(
            200,
            json={"ok": True, "proof": "!mock_minted_proof", "poolState": "hot"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    signer = RemoteProofSigner(server_url="https://mock.proof.server")

    with patch("httpx.AsyncClient", return_value=mock_client):
        proof = await signer.mint_proof(
            cookies={"SAPISID": "test_cookie"},
            auth_user="0",
            tier="Free",
            model="models/gemini-3.7-flash",
            prompt="hello",
            wire_contents=[[[[None, "hello"]], "user"]],
        )

    assert proof == "!mock_minted_proof"
    await signer.close()


@pytest.mark.asyncio
async def test_remote_proof_signer_error():
    """Verify RemoteProofSigner raises BrowserTransportError on upstream failure."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"ok": False, "error": "Cookie expired or invalid"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    signer = RemoteProofSigner(server_url="https://mock.proof.server")

    with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(
        BrowserTransportError, match="Cookie expired or invalid"
    ):
        await signer.mint_proof(
            cookies={"SAPISID": "expired"},
            auth_user="0",
            prompt="test",
        )
    await signer.close()


@pytest.mark.asyncio
async def test_remote_proof_signer_retry_on_network_error():
    """Verify RemoteProofSigner retries on transient connection error."""
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("Network unreachable")
        return httpx.Response(
            200,
            json={"ok": True, "proof": "!mock_recovered_proof", "poolState": "cold"},
        )

    signer = RemoteProofSigner(server_url="https://mock.proof.server")

    orig_client = httpx.AsyncClient
    with patch(
        "aistudio_api.infrastructure.waa.signer.httpx.AsyncClient",
        side_effect=lambda **kwargs: orig_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    ):
        proof = await signer.mint_proof(
            cookies={"SAPISID": "valid_cookie"},
            prompt="retry test",
        )
    assert proof == "!mock_recovered_proof"
    await signer.close()


@pytest.mark.asyncio
async def test_remote_proof_signer_check_health():
    """Verify RemoteProofSigner check_health sends X-Google-Cookies and parses ok."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["X-Google-Cookies"] == "SAPISID=active"
        assert request.headers["X-Goog-AuthUser"] == "1"
        return httpx.Response(200, json={"ok": True, "hotTtlMs": 3600000})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    signer = RemoteProofSigner(server_url="https://mock.proof.server")

    with patch("httpx.AsyncClient", return_value=mock_client):
        healthy = await signer.check_health(cookies={"SAPISID": "active"}, auth_user="1")

    assert healthy is True
    await signer.close()


@pytest.mark.asyncio
async def test_waa_signer_facade():
    """Verify WaaSigner delegates generate_snapshot to RemoteProofSigner."""
    signer = WaaSigner(proof_server_url="https://mock.proof.server")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "proof": "!facade_token"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with patch("httpx.AsyncClient", return_value=mock_client):
        token = await signer.generate_snapshot(
            contents=[AistudioContent(role="user", parts=[AistudioPart(text="ping")])],
            cookies={"SAPISID": "session_cookie"},
            auth_user="0",
            tier="Free",
            model="models/gemini-3.7-flash",
        )

    assert token == "!facade_token"
    await signer.close()
