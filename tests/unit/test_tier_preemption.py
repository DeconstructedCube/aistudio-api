"""Unit tests for tier preemption and paid model fast-fail."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import ensure_active_account
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
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


def test_is_paid_model_handles_mixed_access_modes():
    """accessModes containing 3 or 4 alongside 1 must still be treated as paid."""
    from aistudio_api.infrastructure.gateway.model_discovery import (
        ModelDiscoveryService,
    )

    svc = ModelDiscoveryService()
    svc._cached_models = [
        {
            "id": "custom-mixed-paid",
            "name": "models/custom-mixed-paid",
            "accessModes": [1, 3],
        }
    ]
    assert svc.is_paid_model("custom-mixed-paid") is True
    assert svc.get_required_tiers("custom-mixed-paid") == ["Pro", "Ultra", "Ultra_20X"]
    assert svc.is_model_allowed_for_tier("custom-mixed-paid", "pro") is True
    assert svc.is_model_allowed_for_tier("custom-mixed-paid", "Free") is False


def test_record_rotator_event_explicit_account_id_avoids_concurrent_misattribution():
    """Explicit account_id must attribute events to the request account even after active account switches."""
    from aistudio_api.application.account_orchestrator import record_rotator_event

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_service.get_active_account.return_value = AccountMeta(
        id="acc_new_active",
        name="New Active",
        email="new@gmail.com",
        created_at="2026-01-01",
    )

    with (
        patch.object(runtime_state, "rotator", mock_rotator),
        patch.object(runtime_state, "account_service", mock_service),
    ):
        record_rotator_event(
            "rate_limited", model="gemini-3.7-flash", account_id="acc_original"
        )
        mock_rotator.record_rate_limited.assert_called_once_with(
            "acc_original", model="gemini-3.7-flash"
        )


@pytest.mark.asyncio
async def test_detect_account_tier_preserves_paid_tier_on_probe_failure(tmp_path):
    """Transient probe errors must not downgrade existing Pro/Ultra_20X accounts to Free."""
    import httpx

    from aistudio_api.api.routes_accounts import detect_account_tier
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.account.cookie_parser import (
        probe_account_benefit_tier,
    )

    def failing_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(failing_handler)
    ) as mock_client:
        probed = await probe_account_benefit_tier(
            {"SID": "s1", "SAPISID": "sap1"}, client=mock_client, default=None
        )
        assert probed is None

    store = AccountStore(accounts_dir=tmp_path / "accounts_tier_preserve")
    svc = AccountService(store)
    acc = svc.save_account_from_cookies(
        name="Ultra 20X Acc",
        email="u20@gmail.com",
        storage_state={"cookies": [{"name": "SID", "value": "s1"}]},
        tier="Ultra_20X",
    )
    with patch(
        "aistudio_api.api.routes_accounts.probe_account_benefit_tier",
        new=AsyncMock(return_value=None),
    ):
        res = await detect_account_tier(acc.id, account_service=svc)
        assert res.tier == "Ultra_20X"


@pytest.mark.asyncio
async def test_paid_model_retry_preempts_back_when_concurrent_free_model_switched_active_account(
    tmp_path,
):
    """When a concurrent free-model request switches the active account to Free mid-retry,
    ensure_active_account on attempt >= 1 must still preempt back to a Pro/Ultra account
    and return a pinned SessionContext for the request.
    """
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.gateway.session import GatewaySession

    store = AccountStore(accounts_dir=tmp_path / "accounts_mixed_retry")
    free_acc = store.save_account(
        name="Free Account",
        email="free@gmail.com",
        storage_state={"cookies": [{"name": "SAPISID", "value": "sap_free"}]},
        auth_user="0",
        tier="Free",
    )
    pro_acc = store.save_account(
        name="Pro Account",
        email="pro@gmail.com",
        storage_state={"cookies": [{"name": "SAPISID", "value": "sap_pro"}]},
        auth_user="1",
        tier="Pro",
    )
    svc = AccountService(store)
    rotator = AccountRotator(store)
    session = GatewaySession()
    mock_client = MagicMock()
    mock_client.session = session

    # Simulate concurrent free model having switched active account to free_acc before attempt=1
    svc.set_active_account(free_acc.id)
    await svc.activate_account(free_acc.id, session)

    with (
        patch.object(runtime_state, "rotator", rotator),
        patch.object(runtime_state, "account_service", svc),
        patch.object(runtime_state, "client", mock_client),
    ):
        pinned_ctx = await ensure_active_account(
            attempt=1, model="models/gemini-4-argon"
        )
        assert pinned_ctx is not None
        assert pinned_ctx.tier == "Pro"
        assert pinned_ctx.auth_user == "1"
        assert svc.get_active_account() is not None
        assert svc.get_active_account().id == pro_acc.id  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_session_signer_headers_isolated_per_account_context():
    """Dynamic signer headers (Visit-Id / Ext-Bin) must be isolated per SessionContext."""
    from aistudio_api.infrastructure.gateway.client import AIStudioClient
    from aistudio_api.infrastructure.gateway.session import (
        GatewaySession,
        SessionContext,
    )
    from aistudio_api.infrastructure.gateway.wire_types import (
        AistudioContent,
        AistudioPart,
    )

    session = GatewaySession()
    ctx_a = SessionContext(
        auth_user="0",
        auth_file="/virtual/acc_a/auth.json",
        tier="Pro",
        cookies={"SAPISID": "sap_a"},
    )
    ctx_b = SessionContext(
        auth_user="1",
        auth_file="/virtual/acc_b/auth.json",
        tier="Free",
        cookies={"SAPISID": "sap_b"},
    )

    async def fake_sign(**kwargs):
        u = kwargs.get("auth_user")
        session._signer._remote_signer.last_headers = {
            "X-AIStudio-Visit-Id": f"visit_u{u}",
            "X-Goog-Ext-519733851-Bin": f"ext_u{u}",
        }
        session._signer._remote_signer.last_pool_state = "hot"
        return f"!proof_u{u}"

    session._signer.generate_snapshot = AsyncMock(side_effect=fake_sign)
    contents = [AistudioContent(role="user", parts=[AistudioPart(text="hi")])]

    await session.generate_snapshot(contents, ctx=ctx_a, model="models/gemini-4-argon")
    await session.generate_snapshot(
        contents, ctx=ctx_b, model="models/gemini-3.8-flash"
    )

    client = AIStudioClient()
    client._session = session
    headers_a = client._build_upstream_headers(ctx_a)
    headers_b = client._build_upstream_headers(ctx_b)

    assert headers_a["X-AIStudio-Visit-Id"] == "visit_u0"
    assert headers_a["X-Goog-Ext-519733851-Bin"] == "ext_u0"
    assert headers_a["X-Goog-AuthUser"] == "0"
    assert headers_b["X-AIStudio-Visit-Id"] == "visit_u1"
    assert headers_b["X-Goog-Ext-519733851-Bin"] == "ext_u1"
    assert headers_b["X-Goog-AuthUser"] == "1"
