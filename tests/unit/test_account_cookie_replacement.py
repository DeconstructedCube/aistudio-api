from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.api.routes_accounts import (
    ProbeAndImportRequest,
    delete_cookie_group,
    probe_and_import,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta


@pytest.mark.asyncio
async def test_deleting_active_cookie_group_detaches_browser_session():
    active = AccountMeta(
        id="acc_old",
        name="Old u/5",
        email=None,
        created_at="2026-09-26T00:00:00+00:00",
        auth_user="5",
        cookie_id="cookie_old",
    )
    account_service = MagicMock()
    account_service.list_accounts.side_effect = [[active], []]
    account_service.get_active_account.return_value = active
    account_service.delete_account.return_value = True
    browser_session = MagicMock()
    browser_session.switch_auth = AsyncMock()
    runtime_state = MagicMock()
    runtime_state.client._session = browser_session

    result = await delete_cookie_group(
        "cookie_old",
        account_service=account_service,
        runtime_state=runtime_state,
    )

    assert result == {"deleted": 1}
    browser_session.switch_auth.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_probe_import_activates_first_account_when_registry_was_empty(
    monkeypatch,
):
    imported = AccountMeta(
        id="acc_new",
        name="New u/0",
        email=None,
        created_at="2026-09-27T00:00:00+00:00",
        auth_user="0",
        cookie_id="cookie_new",
    )
    account_service = MagicMock()
    account_service.get_active_account.return_value = None
    account_service.batch_import_accounts.return_value = [imported]
    account_service.activate_account = AsyncMock(return_value=imported)
    browser_session = MagicMock()
    runtime_state = MagicMock()
    runtime_state.client._session = browser_session

    from aistudio_api.infrastructure.account import cookie_parser

    monkeypatch.setattr(
        cookie_parser,
        "probe_google_accounts_infinite",
        AsyncMock(return_value=[{"auth_user": "0", "status_code": 200}]),
    )

    response = await probe_and_import(
        ProbeAndImportRequest(cookies="SAPISID=test-value"),
        account_service=account_service,
        runtime_state=runtime_state,
    )

    assert response.imported_count == 1
    account_service.activate_account.assert_awaited_once_with(
        "acc_new", browser_session
    )
