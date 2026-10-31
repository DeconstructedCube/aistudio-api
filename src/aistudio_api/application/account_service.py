"""账号管理应用服务，协调 account_store 与 Cookie 导入。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountPreset,
        AccountStore,
    )
    from aistudio_api.infrastructure.gateway.session import GatewaySession
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
        session: GatewaySession,
    ) -> AccountMeta | None:
        """切换到指定账号。"""
        account = self._store.get_account(account_id)
        if account is None:
            return None

        auth_path = self._store.get_auth_path_optional(account_id, require_exists=False)
        if auth_path is None:
            logger.error("账号 %s 的账号目录不存在", account_id)
            return None

        self._store.set_active_account(account_id)
        logger.info("已切换活跃账号持久化状态: %s", account_id)
        await session.switch_auth(str(auth_path))
        await session.ensure_ready()
        logger.info("已切换到账号: %s (%s)", account_id, account.name)
        return account
    def delete_account(self, account_id: str) -> bool:
        """删除账号。"""
        return self._store.delete_account(account_id)

    def update_account(self, account_id: str, name: str) -> AccountMeta | None:
        """更新账号名称。"""
        return self._store.update_account(account_id, name)

    def update_account_email(self, account_id: str, email: str) -> bool:
        """更新账号的登录邮箱。"""
        return self._store.update_account_email(account_id, email)

    def update_account_tier(self, account_id: str, tier: str) -> bool:
        """更新账号的订阅权益等级 (Free / Pro / Ultra / Plus)。"""
        return self._store.update_account_tier(account_id, tier)

    def save_account_from_cookies(
        self,
        *,
        name: str,
        email: str | None,
        storage_state: dict[str, object],
        account_id: str | None = None,
        auth_user: str = "0",
        cookie_id: str | None = None,
        tier: str = "Free",
        enabled: bool = True,
        group: str = "default",
        notes: str = "",
    ) -> AccountMeta:
        """从 Cookie 存储状态保存新账号。"""
        return self._store.save_account(
            name=name,
            email=email,
            storage_state=storage_state,
            account_id=account_id,
            auth_user=auth_user,
            cookie_id=cookie_id,
            tier=tier,
            enabled=enabled,
            group=group,
            notes=notes,
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
            tier = str(p.get("tier") or "Free")
            email_val = str(p["email"]) if p.get("email") else None
            acc_name = (
                f"{prefix} (u/{u_idx})"
                if len(probed_list) > 1 or u_idx != "0"
                else prefix
            )
            account = self._store.save_account(
                name=acc_name,
                email=email_val,
                storage_state=storage_state,
                auth_user=u_idx,
                cookie_id=cookie_id,
                tier=tier,
            )
            imported.append(account)

        if not self.get_active_account() and imported:
            self.set_active_account(imported[0].id)
        return imported

    def get_account_auth_path(self, account_id: str) -> Path | None:
        """获取指定账号的 auth.json 路径。"""
        return self._store.get_auth_path(account_id)

    def toggle_account_enabled(
        self, account_id: str, enabled: bool | None = None
    ) -> AccountMeta | None:
        """切换账号的启用/禁用状态。"""
        return self._store.toggle_account_enabled(account_id, enabled)

    def update_account_group(self, account_id: str, group: str) -> AccountMeta | None:
        """更新账号所属分组。"""
        return self._store.update_account_group(account_id, group)

    def update_account_notes(self, account_id: str, notes: str) -> AccountMeta | None:
        """更新账号运维备注。"""
        return self._store.update_account_notes(account_id, notes)

    def list_groups(self) -> list[str]:
        """列出当前存在的所有分组。"""
        return self._store.list_groups()

    def batch_toggle_group(self, group: str, enabled: bool) -> list[AccountMeta]:
        """按分组批量切换使能状态。"""
        return self._store.batch_toggle_group(group, enabled)

    def list_presets(self) -> list[AccountPreset]:
        """列出所有预设方案。"""
        return self._store.list_presets()

    def get_preset(self, preset_id: str) -> AccountPreset | None:
        """获取单个预设方案。"""
        return self._store.get_preset(preset_id)

    def save_preset(self, preset: AccountPreset) -> AccountPreset:
        """保存或更新预设方案。"""
        return self._store.save_preset(preset)

    def delete_preset(self, preset_id: str) -> bool:
        """删除自定义预设。"""
        return self._store.delete_preset(preset_id)

    def create_snapshot_preset(self, name: str, description: str = "") -> AccountPreset:
        """快照保存当前所有已启用账号。"""
        return self._store.create_snapshot_preset(name, description)

    def apply_preset(
        self, preset_id: str
    ) -> tuple[AccountPreset, list[str], list[str]]:
        """一键应用预设方案。"""
        return self._store.apply_preset(preset_id)
