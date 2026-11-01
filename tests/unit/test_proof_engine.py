from unittest.mock import AsyncMock, patch

import pytest

from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_proof_engine_settings,
    update_configured_proof_engine_settings,
)
from aistudio_api.infrastructure.gateway.remote_proof import (
    RemoteProofClient,
    RemoteProofError,
    _format_cookies,
    _serialize_contents_to_messages,
)


def test_format_cookies():
    # String
    assert _format_cookies("a=1; b=2") == "a=1; b=2"
    # Dict
    assert _format_cookies({"a": "1", "b": "2"}) == "a=1; b=2"
    # List of dicts (auth.json format)
    assert _format_cookies([{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]) == "a=1; b=2"
    # Empty
    assert _format_cookies(None) == ""


def test_serialize_contents_to_messages():
    # String
    assert _serialize_contents_to_messages("hello") == [{"role": "user", "content": "hello"}]
    # None
    assert _serialize_contents_to_messages(None) == [{"role": "user", "content": "hi"}]
    # Dicts
    items = [{"role": "model", "parts": [{"text": "response"}]}]
    assert _serialize_contents_to_messages(items) == [{"role": "model", "content": "response"}]


def test_proof_engine_settings_roundtrip(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("proof_engine:\n  provider: browser\n  remote_url: https://waa.spinsnow.fun\n", encoding="utf-8")

    initial = get_configured_proof_engine_settings(cfg_file)
    assert initial["provider"] == "browser"
    assert initial["auto_fallback"] is True

    updated = update_configured_proof_engine_settings(
        provider="remote",
        remote_url="https://custom.proof.node",
        auto_fallback=False,
        timeout_seconds=20,
        config_path=cfg_file,
    )
    assert updated["provider"] == "remote"
    assert updated["remote_url"] == "https://custom.proof.node"
    assert updated["auto_fallback"] is False
    assert updated["timeout_seconds"] == 20

    reloaded = get_configured_proof_engine_settings(cfg_file)
    assert reloaded["provider"] == "remote"
    assert reloaded["remote_url"] == "https://custom.proof.node"
    assert reloaded["auto_fallback"] is False


@pytest.mark.asyncio
async def test_remote_proof_client_mint_mock():
    client = RemoteProofClient(default_url="https://mock.test")
    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.json = lambda: {"ok": True, "proof": "!testproof123", "poolState": "hot"}

    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        proof = await client.mint_proof(
            cookies="SID=123",
            model="models/gemini-3.8-flash",
            contents="hello",
        )
        assert proof == "!testproof123"
    await client.close()


@pytest.mark.asyncio
async def test_remote_proof_client_handles_error():
    client = RemoteProofClient(default_url="https://mock.test")
    fake_resp = AsyncMock()
    fake_resp.status_code = 500
    fake_resp.text = "Internal error"

    with (
        patch("httpx.AsyncClient.post", return_value=fake_resp),
        pytest.raises(RemoteProofError, match="HTTP 500"),
    ):
        await client.mint_proof(
            cookies="SID=123",
            model="models/gemini-3.8-flash",
        )
    await client.close()
