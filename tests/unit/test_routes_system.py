"""Tests for system and configuration routes."""

from __future__ import annotations

import httpx
import pytest

from aistudio_api.api.app import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


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
            assert "Accept" in resp.headers.get("Vary", "")
            assert "no-store" in resp.headers.get("Cache-Control", "")


@pytest.mark.asyncio
async def test_get_config_endpoint():
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "port" in data
        assert "proof_server_url" in data
        assert "yaml_content" in data


@pytest.mark.asyncio
async def test_update_config_yaml_validation():
    async with _client() as client:
        resp = await client.put(
            "/config/yaml", json={"yaml_content": "invalid: [yaml: broken"}
        )
        assert resp.status_code == 400

        resp_non_dict = await client.put(
            "/config/yaml", json={"yaml_content": "- item1\n- item2"}
        )
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

        resp_clear = await client.post(
            "/rotation/clear-cooldown", json={"account_id": "acc_1"}
        )
        assert resp_clear.status_code == 200
        mock_rotator.clear_cooldown.assert_called_once_with("acc_1", model=None)


@pytest.mark.asyncio
async def test_get_config_defaults_and_yaml_section_validation():
    async with _client() as client:
        resp = await client.get("/config/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert "tier_quotas" in data
        assert "paid_model_rules" in data
        assert "rotator" in data
        assert "model_defaults" in data
        assert data["tier_quotas"]["Free"]["hydrogen_models"]["daily_limit"] == 50
        assert data["tier_quotas"]["Free"]["hydrogen_models"]["daily_token_limit"] == 10_000_000

        bad_quota_resp = await client.put(
            "/config/yaml",
            json={"yaml_content": "tier_quotas:\n  Free:\n    flash_models:\n      daily_limit: -5\n"},
        )
        assert bad_quota_resp.status_code == 400


def test_rotator_scores_token_exhaustion_alongside_request_count(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from aistudio_api.application.account_rotator import AccountRotator, AccountStats
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )
    from aistudio_api.infrastructure.gateway.model_defaults import (
        invalidate_config_cache,
    )

    cfg = tmp_path / "quota_cfg.yaml"
    cfg.write_text(
        """
rotator:
  strategy: sticky
  stickiness_bonus: 400.0
  stickiness_threshold: 0.8
  drip_penalty: 500.0
  tier_weights:
    Free: 0.0
  cooldowns:
    rate_limited_short: 60
    rate_limited_drip: 300
    rate_limited_drip_extended: 600
tier_quotas:
  Free:
    hydrogen_models:
      match:
        contains: [hydrogen]
      daily_limit: 50
      daily_token_limit: 10000000
    default_limit: 20
"""
    )
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(cfg))
    invalidate_config_cache()

    store = MagicMock(spec=AccountStore)
    acc_busy_tok = AccountMeta(
        id="acc_1",
        name="BusyToken",
        email="busy@example.com",
        tier="Free",
        created_at="2026-01-01",
    )
    acc_fresh = AccountMeta(
        id="acc_2",
        name="Fresh",
        email="fresh@example.com",
        tier="Free",
        created_at="2026-01-01",
    )
    store.list_accounts.return_value = [acc_busy_tok, acc_fresh]

    rotator = AccountRotator(store)
    st1 = AccountStats(account_id="acc_1")
    # Only 5 requests, but 9M / 10M tokens (90% > stickiness_threshold 0.8)
    st1.record_success(model="gemini-4-hydrogen", usage={"prompt_tokens": 9_000_000, "completion_tokens": 0, "total_tokens": 9_000_000})
    st2 = AccountStats(account_id="acc_2")

    score1 = rotator._score_candidate(acc_busy_tok, st1, "gemini-4-hydrogen", current_account_id="acc_1")
    score2 = rotator._score_candidate(acc_fresh, st2, "gemini-4-hydrogen", current_account_id="acc_1")
    assert score2 < score1
