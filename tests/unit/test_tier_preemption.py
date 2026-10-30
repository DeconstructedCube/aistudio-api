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
