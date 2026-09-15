"""账号管理应用服务，协调 account_store 与 Cookie 导入。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
    from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
logger = logging.getLogger("aistudio.account")


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

            await browser_session.switch_auth(str(auth_path))
            await browser_session.ensure_context()

            if not keep_snapshot_cache and snapshot_cache is not None:
                snapshot_cache.clear()
                logger.info("已清除 snapshot 缓存")

            self._store.set_active_account(account_id)
            logger.info("已切换到账号: %s (%s)", account_id, account.name)
            return account

        return await _do_switch()

    def delete_account(self, account_id: str) -> bool:
        """删除账号。"""
        return self._store.delete_account(account_id)

    def update_account(self, account_id: str, name: str) -> AccountMeta | None:
        """更新账号名称。"""
        return self._store.update_account(account_id, name)
