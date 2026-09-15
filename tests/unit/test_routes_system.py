"""Tests for system and configuration routes."""

from __future__ import annotations

import httpx
import pytest

from aistudio_api.api.app import app

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

@pytest.mark.asyncio
async def test_health_endpoint():
    async with _client() as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_check_endpoint():
    async with _client() as client:
        resp = await client.get("/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_enabled" in data


@pytest.mark.asyncio
async def test_spa_endpoints():
    import asyncio

    async with _client() as client:
        responses = await asyncio.gather(
            *[
                client.get(path, headers={"Accept": "text/html"})
                for path in ("/", "/login", "/accounts", "/settings")
            ]
        )
        for resp in responses:
            assert resp.status_code == 200
            assert "<!DOCTYPE html>" in resp.text


@pytest.mark.asyncio
async def test_get_config_endpoint():
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "port" in data
        assert "browser_port" in data
        assert "yaml_content" in data


@pytest.mark.asyncio
async def test_update_config_yaml_validation():
    async with _client() as client:
        resp = await client.put("/config/yaml", json={"yaml_content": "invalid: [yaml: broken"})
        assert resp.status_code == 400

        resp_non_dict = await client.put("/config/yaml", json={"yaml_content": "- item1\n- item2"})
        assert resp_non_dict.status_code == 400

@pytest.mark.asyncio
async def test_rotation_status_and_clear_cooldown():
    from unittest.mock import MagicMock
    from aistudio_api.api.state import runtime_state
    from aistudio_api.application.account_rotator import AccountRotator

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_rotator.get_all_stats.return_value = {"acc_1": {"requests": 1}}

    runtime_state.rotator = mock_rotator
    async with _client() as client:
        resp = await client.get("/rotation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "sticky"
        assert "accounts" in data

        resp_clear = await client.post("/rotation/clear-cooldown", json={"account_id": "acc_1"})
        assert resp_clear.status_code == 200
        mock_rotator.clear_cooldown.assert_called_once_with("acc_1", model=None)
