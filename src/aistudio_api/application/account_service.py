"""账号管理应用服务，协调 account_store 与 Cookie 导入。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )
    from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
    from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("account")


class AccountService:
    """账号管理服务。"""

    def __init__(
        self,
        account_store: AccountStore,
    ) -> None:
        self._store = account_store

    def list_accounts(self) -> list[AccountMeta]:
        """列出所有账号。"""
        return self._store.list_accounts()

    def get_account(self, account_id: str) -> AccountMeta | None:
        """获取单个账号。"""
        return self._store.get_account(account_id)

    def get_active_account(self) -> AccountMeta | None:
        """获取当前活跃账号。"""
        return self._store.get_active_account()

    def set_active_account(self, account_id: str) -> None:
        """设置当前活跃账号。"""
        self._store.set_active_account(account_id)

    async def activate_account(
        self,
        account_id: str,
        browser_session: BrowserSession,
        snapshot_cache: SnapshotCache | None,
        _unused_lock: object = None,
        keep_snapshot_cache: bool = False,
    ) -> AccountMeta | None:
        """切换到指定账号。"""
        account = self._store.get_account(account_id)
        if account is None:
            return None

        async def _do_switch():
            auth_path = self._store.get_auth_path_optional(
                account_id, require_exists=False
            )
            if auth_path is None:
                logger.error("账号 %s 的账号目录不存在", account_id)
                return None

            self._store.set_active_account(account_id)
            if not keep_snapshot_cache:
                if snapshot_cache is not None:
                    snapshot_cache.clear()
                from aistudio_api.api.state import runtime_state

                if runtime_state.client is not None:
                    runtime_state.client.clear_snapshot_cache()
                logger.info("已清除 snapshot 与模板缓存")

            await browser_session.switch_auth(str(auth_path))
            await browser_session.ensure_botguard_service()
            logger.info("已切换到账号: %s (%s)", account_id, account.name)
            return account

        return await _do_switch()

    def delete_account(self, account_id: str) -> bool:
        """删除账号。"""
        return self._store.delete_account(account_id)

    def update_account(self, account_id: str, name: str) -> AccountMeta | None:
        """更新账号名称。"""
        return self._store.update_account(account_id, name)

    def save_account_from_cookies(
        self,
        *,
        name: str,
        email: str | None,
        storage_state: dict[str, object],
        account_id: str | None = None,
        auth_user: str = "0",
        cookie_id: str | None = None,
    ) -> AccountMeta:
        """从 Cookie 存储状态保存新账号。"""
        return self._store.save_account(
            name=name,
            email=email,
            storage_state=storage_state,
            account_id=account_id,
            auth_user=auth_user,
            cookie_id=cookie_id,
        )

    def batch_import_accounts(
        self,
        probed_list: list[dict[str, object]],
        storage_state: dict[str, object],
        prefix: str = "Google Account",
        cookie_id: str | None = None,
    ) -> list[AccountMeta]:
        """批量导入探活到的账号列表。"""
        imported: list[AccountMeta] = []
        for p in probed_list:
            u_idx = str(p.get("auth_user") or "0")
            acc_name = (
                f"{prefix} (u/{u_idx})"
                if len(probed_list) > 1 or u_idx != "0"
                else prefix
            )
            account = self._store.save_account(
                name=acc_name,
                email=None,
                storage_state=storage_state,
                auth_user=u_idx,
                cookie_id=cookie_id,
            )
            imported.append(account)

        if not self.get_active_account() and imported:
            self.set_active_account(imported[0].id)
        return imported

    def get_account_auth_path(self, account_id: str) -> Path | None:
        """获取指定账号的 auth.json 路径。"""
        return self._store.get_auth_path(account_id)
