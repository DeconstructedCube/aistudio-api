"""Unit tests for tier preemption and paid model fast-fail."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import ensure_active_account
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta


@pytest.mark.asyncio
async def test_free_account_requesting_paid_model_preempts_to_pro_account():
    """当当前账号为 Free 等级但收到付费模型（如生图/Argon）时，本地直接主动抢切 Pro/Ultra 账号。"""
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock()
    mock_client.session = MagicMock()

    free_acc = AccountMeta(
        id="acc_free",
        name="Free Account",
        email="free@gmail.com",
        tier="Free",
        created_at="2026-01-01",
    )
    pro_acc = AccountMeta(
        id="acc_pro",
        name="Pro Account",
        email="pro@gmail.com",
        tier="Pro",
        created_at="2026-01-01",
    )

    mock_service.get_active_account.return_value = free_acc
    mock_service.activate_account = AsyncMock(return_value=pro_acc)
    mock_rotator.get_next_account = AsyncMock(return_value=pro_acc)
    mock_rotator.clear_cooldown = MagicMock()

    with (
        patch.object(runtime_state, "rotator", mock_rotator),
        patch.object(runtime_state, "account_service", mock_service),
        patch.object(runtime_state, "client", mock_client),
    ):
        # 请求付费生图模型
        await ensure_active_account(attempt=0, model="gemini-3.1-flash-image-preview")

        # 验证主动抢切被触发，并且传入了所需付费层级
        mock_rotator.get_next_account.assert_awaited_once()
        kwargs = mock_rotator.get_next_account.call_args[1]
        assert "Pro" in kwargs["required_tiers"] and "Ultra" in kwargs["required_tiers"]
        mock_service.activate_account.assert_awaited_once_with(
            "acc_pro", mock_client.session
        )


@pytest.mark.asyncio
async def test_free_account_requesting_paid_model_fast_fails_when_no_pro_account():
    """当请求付费模型但账号池中没有任何 Pro/Ultra 账号时，本地直接友好报 403 Fast-Fail，不上报上游。"""
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock()
    mock_client.session = MagicMock()

    free_acc = AccountMeta(
        id="acc_free",
        name="Free Account",
        email="free@gmail.com",
        tier="Free",
        created_at="2026-01-01",
    )

    mock_service.get_active_account.return_value = free_acc
    # 没有可用的 Pro 账号，返回 None
    mock_rotator.get_next_account = AsyncMock(return_value=None)

    with (
        patch.object(runtime_state, "rotator", mock_rotator),
        patch.object(runtime_state, "account_service", mock_service),
        patch.object(runtime_state, "client", mock_client),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await ensure_active_account(
                attempt=0, model="gemini-3.1-flash-image-preview"
            )

        assert exc_info.value.status_code == 403
        assert "需要 Google AI Studio Pro" in str(exc_info.value.detail)
        mock_service.activate_account.assert_not_called()


@pytest.mark.asyncio
async def test_plus_account_requesting_pro_model_preempts_to_pro_account():
    """当当前账号为 Plus 等级但收到 Pro 独占模型时，主动抢切 Pro/Ultra 账号。"""
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock()
    mock_client.session = MagicMock()

    plus_acc = AccountMeta(
        id="acc_plus",
        name="Plus Account",
        email="plus@gmail.com",
        tier="Plus",
        created_at="2026-01-01",
    )
    pro_acc = AccountMeta(
        id="acc_pro",
        name="Pro Account",
        email="pro@gmail.com",
        tier="Pro",
        created_at="2026-01-01",
    )

    mock_service.get_active_account.return_value = plus_acc
    mock_service.activate_account = AsyncMock(return_value=pro_acc)
    mock_rotator.get_next_account = AsyncMock(return_value=pro_acc)
    mock_rotator.clear_cooldown = MagicMock()

    with (
        patch.object(runtime_state, "rotator", mock_rotator),
        patch.object(runtime_state, "account_service", mock_service),
        patch.object(runtime_state, "client", mock_client),
    ):
        await ensure_active_account(attempt=0, model="gemini-3.1-flash-image-preview")

        mock_rotator.get_next_account.assert_awaited_once()
        kwargs = mock_rotator.get_next_account.call_args[1]
        assert "Pro" in kwargs["required_tiers"]
        mock_service.activate_account.assert_awaited_once_with(
            "acc_pro", mock_client.session
        )


@pytest.mark.asyncio
async def test_pro_account_requesting_ultra_model_preempts_to_ultra_account():
    """当当前账号为 Pro 等级但收到 Veo (Ultra 独占) 模型时，主动抢切 Ultra 账号。"""
    mock_rotator = MagicMock(spec=AccountRotator)
    mock_service = MagicMock(spec=AccountService)
    mock_client = MagicMock()
    mock_client.session = MagicMock()

    pro_acc = AccountMeta(
        id="acc_pro",
        name="Pro Account",
        email="pro@gmail.com",
        tier="Pro",
        created_at="2026-01-01",
    )
    ultra_acc = AccountMeta(
        id="acc_ultra",
        name="Ultra Account",
        email="ultra@gmail.com",
        tier="Ultra",
        created_at="2026-01-01",
    )

    mock_service.get_active_account.return_value = pro_acc
    mock_service.activate_account = AsyncMock(return_value=ultra_acc)
    mock_rotator.get_next_account = AsyncMock(return_value=ultra_acc)
    mock_rotator.clear_cooldown = MagicMock()

    with (
        patch.object(runtime_state, "rotator", mock_rotator),
        patch.object(runtime_state, "account_service", mock_service),
        patch.object(runtime_state, "client", mock_client),
    ):
        await ensure_active_account(attempt=0, model="veo-2.0-generate-001")

        mock_rotator.get_next_account.assert_awaited_once()
        kwargs = mock_rotator.get_next_account.call_args[1]
        assert (
            "Ultra" in kwargs["required_tiers"]
            and "Pro" not in kwargs["required_tiers"]
        )
        mock_service.activate_account.assert_awaited_once_with(
            "acc_ultra", mock_client.session
        )


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
