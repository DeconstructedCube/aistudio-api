"""Unit tests for local stdio IPC WAA proof signing client."""

from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aistudio_api.domain.errors import (
    AccountRestrictedError,
    SessionExpiredError,
    TransportError,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart
from aistudio_api.infrastructure.waa.daemon import LocalSignerProcess
from aistudio_api.infrastructure.waa.signer import (
    LocalIpcProofSigner,
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
async def test_local_ipc_proof_signer_success():
    """Verify LocalIpcProofSigner sends proof.mint IPC payload and parses proof token."""
    fake_proc = MagicMock(spec=LocalSignerProcess)

    async def fake_request(method: str, params=None, **_kwargs):
        assert method == "proof.mint"
        assert isinstance(params, dict)
        assert params["cookies"] == "SAPISID=test_cookie"
        assert params["authUser"] == 0
        assert params["prompt"] == "hello"
        assert params["wireContents"] == [[[[None, "hello"]], "user"]]
        return {
            "id": 1,
            "ok": True,
            "proof": "!mock_minted_proof",
            "poolState": "hot",
            "headers": {"X-Aistudio-Visit-Id": "v1_test"},
        }

    fake_proc.request = AsyncMock(side_effect=fake_request)
    signer = LocalIpcProofSigner(process=fake_proc)

    proof = await signer.mint_proof(
        cookies={"SAPISID": "test_cookie"},
        auth_user="0",
        tier="Free",
        model="models/gemini-3.7-flash",
        prompt="hello",
        wire_contents=[[[[None, "hello"]], "user"]],
    )

    assert proof == "!mock_minted_proof"
    assert signer.last_pool_state == "hot"
    assert signer.last_headers == {"X-Aistudio-Visit-Id": "v1_test"}
    await signer.close()


@pytest.mark.asyncio
async def test_local_ipc_proof_signer_logical_error_no_retry():
    """Verify LocalIpcProofSigner raises TransportError immediately on logical WAA failure."""
    fake_proc = MagicMock(spec=LocalSignerProcess)
    fake_proc.request = AsyncMock(
        return_value={"id": 1, "ok": False, "error": "Cookie expired or invalid"}
    )
    signer = LocalIpcProofSigner(process=fake_proc)

    with pytest.raises(TransportError, match="Cookie expired or invalid"):
        await signer.mint_proof(
            cookies={"SAPISID": "expired"},
            auth_user="0",
            prompt="test",
        )
    assert fake_proc.request.await_count == 1
    await signer.close()


@pytest.mark.asyncio
async def test_local_ipc_proof_signer_retries_on_pipe_error():
    """Verify LocalIpcProofSigner retries once on transient IPC pipe failure."""
    fake_proc = MagicMock(spec=LocalSignerProcess)
    fake_proc.request = AsyncMock(
        side_effect=[
            TransportError("waa-signer IPC 管道中断: Broken pipe"),
            {
                "id": 2,
                "ok": True,
                "proof": "!mock_recovered_proof",
                "poolState": "cold",
            },
        ]
    )
    signer = LocalIpcProofSigner(process=fake_proc)

    proof = await signer.mint_proof(
        cookies={"SAPISID": "valid_cookie"},
        prompt="retry test",
    )
    assert proof == "!mock_recovered_proof"
    assert fake_proc.request.await_count == 2
    await signer.close()


@pytest.mark.asyncio
async def test_waa_signer_facade():
    """Verify WaaSigner delegates generate_snapshot to LocalIpcProofSigner."""
    fake_proc = MagicMock(spec=LocalSignerProcess)
    fake_proc.request = AsyncMock(
        return_value={"id": 1, "ok": True, "proof": "!facade_token", "poolState": "hot"}
    )
    signer = WaaSigner(process=fake_proc)

    token = await signer.generate_snapshot(
        contents=[AistudioContent(role="user", parts=[AistudioPart(text="ping")])],
        cookies={"SAPISID": "session_cookie"},
        auth_user="0",
        tier="Free",
        model="models/gemini-3.7-flash",
    )

    assert token == "!facade_token"
    await signer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("err_msg", "expected_exc"),
    [
        (
            "AI Studio 账号服务受限 (servicerestricted): url=https://myaccount.google.com/restrictions/200",
            AccountRestrictedError,
        ),
        (
            "隔离登录态失效 url=https://accounts.google.com/v3/signin",
            SessionExpiredError,
        ),
        (
            "AI Studio 页面缺少 WIu0Nc (url=https://aistudio.google.com/prompts/new_chat)",
            TransportError,
        ),
    ],
)
async def test_local_ipc_proof_signer_classifies_loadpage_errors(
    err_msg: str, expected_exc: type[Exception]
):
    """LocalIpcProofSigner surfaces diagnostic loadPage failures as typed errors."""
    fake_proc = MagicMock(spec=LocalSignerProcess)
    fake_proc.request = AsyncMock(return_value={"id": 1, "ok": False, "error": err_msg})
    signer = LocalIpcProofSigner(process=fake_proc)

    with pytest.raises(expected_exc, match=r"AI Studio|登录态失效"):
        await signer.mint_proof(cookies={"SAPISID": "sap"}, auth_user="0", prompt="hi")
    assert fake_proc.request.await_count == 1
    await signer.close()


@pytest.mark.asyncio
async def test_local_signer_process_stdio_roundtrip(tmp_path):
    """Verify LocalSignerProcess exchanges NDJSON messages over stdio pipes."""
    fake_bin = tmp_path / "waa-signer"
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    stdout_reader = asyncio.StreamReader()
    stderr_reader = asyncio.StreamReader()
    stdout_reader.feed_data(
        b'{"id":0,"event":"ready","ok":true,"engine":"spidermonkey-152"}\n'
    )

    written: list[bytes] = []

    class FakeStdin:
        def write(self, data: bytes) -> None:
            written.append(data)
            req = json.loads(data.decode("utf-8"))
            method = req.get("method")
            req_id = req.get("id")
            if method == "health":
                resp = {
                    "id": req_id,
                    "ok": True,
                    "engine": "spidermonkey-152",
                    "workers": 0,
                }
            elif method == "shutdown":
                resp = {"id": req_id, "ok": True}
            else:
                resp = {
                    "id": req_id,
                    "ok": True,
                    "proof": "!ipc_proof",
                    "poolState": "cold",
                }
            stdout_reader.feed_data((json.dumps(resp) + "\n").encode("utf-8"))
            if method == "shutdown":
                stdout_reader.feed_eof()
                stderr_reader.feed_eof()

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

    fake_subproc = MagicMock()
    fake_subproc.pid = 42424
    fake_subproc.returncode = None
    fake_subproc.stdin = FakeStdin()
    fake_subproc.stdout = stdout_reader
    fake_subproc.stderr = stderr_reader

    async def fake_wait() -> int:
        fake_subproc.returncode = 0
        return 0

    fake_subproc.wait = fake_wait

    proc = LocalSignerProcess(binary_path=fake_bin)
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_subproc)
    ):
        health = await proc.health()
        assert health["ok"] is True
        assert health["engine"] == "spidermonkey-152"
        mint = await proc.request("proof.mint", {"cookies": "SAPISID=x"})
        assert mint["proof"] == "!ipc_proof"
        await proc.stop()
    assert len(written) == 3
