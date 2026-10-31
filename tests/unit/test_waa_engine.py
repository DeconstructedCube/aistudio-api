"""Unit tests for browserless WAA engine challenge decoding and proof signing."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart
from aistudio_api.infrastructure.waa.challenge import (
    compute_interpreter_hash,
    parse_challenge_payload,
)
from aistudio_api.infrastructure.waa.signer import (
    RemoteProofSigner,
    extract_content_digest,
)


def test_challenge_caesar_shift_decoding():
    """Verify Google Caesar shift (+97 modulo 256) challenge decoding."""
    fields = [
        "msg_1234",  # [0] MessageID
        ["var x = 1;"],  # [1] InterpreterJavaScript
        ["//www.google.com/js/bg/mock_hash.js"],  # [2] InterpreterURL
        "mock_hash_123",  # [3] InterpreterHash
        "program_bytecode_data",  # [4] Program
        "botguard_global",  # [5] GlobalName
        None,  # [6]
        '{"experiments": [1, 2]}',  # [7] ClientExperimentsStateBlob
    ]
    raw_json = json.dumps(fields).encode("utf-8")
    # Shift backwards by 97 to simulate Google's obfuscation
    shifted = bytearray(raw_json)
    for i in range(len(shifted)):
        shifted[i] = (shifted[i] - 97) & 0xFF
    encoded_b64 = base64.b64encode(shifted).decode("utf-8")

    outer = [None, encoded_b64]
    challenge = parse_challenge_payload(json.dumps(outer).encode("utf-8"))

    assert challenge.message_id == "msg_1234"
    assert challenge.interpreter_javascript == "var x = 1;"
    assert challenge.interpreter_url == "https://www.google.com/js/bg/mock_hash.js"
    assert challenge.interpreter_hash == "mock_hash_123"
    assert challenge.program == "program_bytecode_data"
    assert challenge.global_name == "botguard_global"
    assert challenge.client_experiments_state_blob == '{"experiments": [1, 2]}'


def test_compute_interpreter_hash():
    """Verify SHA-256 raw urlsafe base64 unpadded digest calculation."""
    sample = b"function botguard() { return 42; }"
    h = compute_interpreter_hash(sample)
    assert "=" not in h
    assert len(h) == 43


def test_extract_content_digest_exact_alignment():
    """Verify content digest only hashes message parts joined by single spaces."""
    contents = [
        AistudioContent(role="user", parts=[AistudioPart(text="hello")]),
        AistudioContent(role="model", parts=[AistudioPart(text="world")]),
    ]
    prompt, digest = extract_content_digest(contents)
    assert prompt == "hello world"
    import hashlib
    assert digest == hashlib.sha256(b"hello world").hexdigest()


@pytest.mark.asyncio
async def test_remote_proof_signer_success():
    """Verify RemoteProofSigner sends payload and parses proof token."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["cookies"] == "SAPISID=test_cookie"
        assert data["authUser"] == 0
        assert data["prompt"] == "hello"
        return httpx.Response(
            200,
            json={"ok": True, "proof": "!mock_minted_proof", "poolState": "hot"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    signer = RemoteProofSigner(server_url="https://mock.proof.server")

    # Patch client creation inside mint_proof
    from unittest.mock import patch

    with patch("httpx.AsyncClient", return_value=mock_client):
        proof = await signer.mint_proof(
            cookies={"SAPISID": "test_cookie"},
            auth_user="0",
            tier="Free",
            model="models/gemini-3.7-flash",
            prompt="hello",
        )

    assert proof == "!mock_minted_proof"


@pytest.mark.asyncio
async def test_remote_proof_signer_error():
    """Verify RemoteProofSigner raises BrowserTransportError on upstream failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"ok": False, "error": "Cookie expired or invalid"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    signer = RemoteProofSigner(server_url="https://mock.proof.server")

    from unittest.mock import patch

    with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(
        BrowserTransportError, match="Cookie expired or invalid"
    ):
        await signer.mint_proof(
            cookies={"SAPISID": "expired"},
            auth_user="0",
            prompt="test",
        )
