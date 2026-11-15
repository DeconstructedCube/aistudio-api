"""Unit tests for tier preemption and paid model fast-fail."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import ensure_active_account
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.session import GatewaySession


async def _tier_runtime(tmp_path, monkeypatch, current_tier, backup_tier=None):
    """权益测试使用真实账号和会话，监视器仅记录公共接口调用。"""
    from aistudio_api.application import account_orchestrator

    store = AccountStore(accounts_dir=tmp_path / "tier_accounts")
    service = AccountService(store)
    current = service.save_account_from_cookies(
        account_id="acc_" + current_tier.lower(),
        name="当前权益账号",
        email="current@example.test",
        tier=current_tier,
        storage_state={"cookies": [{"name": "SID", "value": "synthetic_current_sid"}]},
        cookie_id="current-generation",
    )
    backup = None
    if backup_tier:
        backup = service.save_account_from_cookies(
            account_id="acc_" + backup_tier.lower(),
            name="备用权益账号",
            email="backup@example.test",
            tier=backup_tier,
            storage_state={
                "cookies": [{"name": "SID", "value": "synthetic_backup_sid"}]
            },
            cookie_id="backup-generation",
        )
    session = GatewaySession()
    await service.activate_account(current.id, session)
    activation = AsyncMock(wraps=service.activate_account)
    monkeypatch.setattr(service, "activate_account", activation)
    rotator = AccountRotator(store)
    selection = AsyncMock(wraps=rotator.get_next_account)
    monkeypatch.setattr(rotator, "get_next_account", selection)
    client = MagicMock()
    client.session = session
    monkeypatch.setattr(runtime_state, "account_service", service)
    monkeypatch.setattr(runtime_state, "rotator", rotator)
    monkeypatch.setattr(runtime_state, "client", client)
    monkeypatch.setattr(account_orchestrator, "_last_switch_time", 0.0)
    return service, session, backup, activation, selection, client


@pytest.mark.asyncio
async def test_free_account_requesting_paid_model_preempts_to_pro_account(
    tmp_path, monkeypatch
):
    """权益不满足时选择正确层级账号，并真实交接请求凭据。"""
    service, session, backup, activation, selection, _ = await _tier_runtime(
        tmp_path, monkeypatch, "Free", "Pro"
    )
    assert backup is not None
    await ensure_active_account(attempt=0, model="gemini-3.1-flash-image-preview")

    selection.assert_awaited_once()
    required_tiers = selection.call_args.kwargs["required_tiers"]
    assert "Pro" in required_tiers and "Ultra" in required_tiers
    activation.assert_awaited_once_with(backup.id, session)
    active = service.get_active_account()
    assert active is not None and active.id == backup.id
    assert session.active_context is not None
    assert session.active_context.auth_file == str(
        service.get_account_auth_path(backup.id)
    )
    assert session.active_context.cookie_id == backup.cookie_id
    assert session.active_context.tier == "Pro"


@pytest.mark.asyncio
async def test_free_account_requesting_paid_model_fast_fails_when_no_pro_account(
    tmp_path, monkeypatch
):
    """池中没有就绪付费账号时，本地返回 403 并清空无资格会话。"""
    _, session, _, activation, selection, client = await _tier_runtime(
        tmp_path, monkeypatch, "Free"
    )
    with pytest.raises(HTTPException) as exc_info:
        await ensure_active_account(attempt=0, model="gemini-3.1-flash-image-preview")

    assert exc_info.value.status_code == 403
    assert "需要 Google AI Studio Pro" in str(exc_info.value.detail)
    selection.assert_awaited_once()
    activation.assert_not_awaited()
    client.generate_content.assert_not_called()
    assert not session.is_ready


@pytest.mark.asyncio
async def test_plus_account_requesting_pro_model_preempts_to_pro_account(
    tmp_path, monkeypatch
):
    """权益不满足时选择正确层级账号，并真实交接请求凭据。"""
    service, session, backup, activation, selection, _ = await _tier_runtime(
        tmp_path, monkeypatch, "Plus", "Pro"
    )
    assert backup is not None
    await ensure_active_account(attempt=0, model="gemini-3.1-flash-image-preview")

    selection.assert_awaited_once()
    required_tiers = selection.call_args.kwargs["required_tiers"]
    assert "Pro" in required_tiers and "Plus" not in required_tiers
    activation.assert_awaited_once_with(backup.id, session)
    active = service.get_active_account()
    assert active is not None and active.id == backup.id
    assert session.active_context is not None
    assert session.active_context.auth_file == str(
        service.get_account_auth_path(backup.id)
    )
    assert session.active_context.cookie_id == backup.cookie_id
    assert session.active_context.tier == "Pro"


@pytest.mark.asyncio
async def test_pro_account_requesting_ultra_model_preempts_to_ultra_account(
    tmp_path, monkeypatch
):
    """权益不满足时选择正确层级账号，并真实交接请求凭据。"""
    service, session, backup, activation, selection, _ = await _tier_runtime(
        tmp_path, monkeypatch, "Pro", "Ultra"
    )
    assert backup is not None
    await ensure_active_account(attempt=0, model="veo-2.0-generate-001")

    selection.assert_awaited_once()
    required_tiers = selection.call_args.kwargs["required_tiers"]
    assert "Ultra" in required_tiers and "Pro" not in required_tiers
    activation.assert_awaited_once_with(backup.id, session)
    active = service.get_active_account()
    assert active is not None and active.id == backup.id
    assert session.active_context is not None
    assert session.active_context.auth_file == str(
        service.get_account_auth_path(backup.id)
    )
    assert session.active_context.cookie_id == backup.cookie_id
    assert session.active_context.tier == "Ultra"
