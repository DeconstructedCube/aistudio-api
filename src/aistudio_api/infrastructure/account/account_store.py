"""账号存储层，管理多 Google 账号的注册表和 storage state。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 默认搜索路径（与 config.py 保持一致）
_SEARCH_ROOTS: list[Path] = [
    Path.cwd(),
    Path(__file__).resolve().parents[4],  # src/aistudio_api/infrastructure/account -> 项目根
]


def _resolve_accounts_dir() -> Path:
    """发现 accounts 目录，默认为 data/accounts。"""
    env = os.getenv("AISTUDIO_ACCOUNTS_DIR")
    if env:
        return Path(env).resolve()
    for root in _SEARCH_ROOTS:
        candidate = root / "data" / "accounts"
        if candidate.is_dir():
            return candidate
    # 默认在第一个搜索根下创建
    return (_SEARCH_ROOTS[0] / "data" / "accounts").resolve()


def _resolve_legacy_auth_file() -> Path | None:
    """查找遗留的 data/auth.json 文件。"""
    for root in _SEARCH_ROOTS:
        candidate = root / "data" / "auth.json"
        if candidate.is_file():
            return candidate
    return None


def _generate_account_id() -> str:
    """生成 acc_ 前缀的随机 ID。"""
    import secrets
    return f"acc_{secrets.token_hex(4)}"


@dataclass
class AccountMeta:
    """账号元数据。"""
    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None = None
    auth_user: str = "0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccountMeta:
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**fields)

@dataclass
class Registry:
    """账号注册表。"""
    accounts: dict[str, AccountMeta]
    active_account_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accounts": {k: v.to_dict() for k, v in self.accounts.items()},
            "active_account_id": self.active_account_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Registry:
        accounts = {
            k: AccountMeta.from_dict(v) for k, v in data.get("accounts", {}).items()
        }
        return cls(
            accounts=accounts,
            active_account_id=data.get("active_account_id"),
        )


class AccountStore:
    """账号存储管理器。"""

    def __init__(self, accounts_dir: Path | None = None) -> None:
        self._accounts_dir = accounts_dir or _resolve_accounts_dir()
        self._registry_path = self._accounts_dir / "registry.json"
        self._registry: Registry | None = None
        self._ensure_dirs()
        self._migrate_legacy_if_needed()

    def _ensure_dirs(self) -> None:
        """确保目录存在。"""
        self._accounts_dir.mkdir(parents=True, exist_ok=True)

    def _migrate_legacy_if_needed(self) -> None:
        """如果 accounts 目录为空且存在 data/auth.json，自动迁移。"""
        if self._registry_path.exists():
            return  # 已有注册表，无需迁移
        legacy = _resolve_legacy_auth_file()
        if legacy is None:
            return
        # 创建一个迁移账号
        account_id = "acc_migrated"
        now = datetime.now(timezone.utc).isoformat()
        meta = AccountMeta(
            id=account_id,
            name="迁移的账号",
            email=None,
            created_at=now,
            last_used=now,
        )
        account_dir = self._accounts_dir / account_id
        account_dir.mkdir(parents=True, exist_ok=True)
        # 复制 auth.json
        shutil.copy2(legacy, account_dir / "auth.json")
        # 写入 meta.json
        (account_dir / "meta.json").write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 创建注册表
        registry = Registry(
            accounts={account_id: meta},
            active_account_id=account_id,
        )
        self._save_registry(registry)

    def _load_registry(self) -> Registry:
        """加载注册表。"""
        if self._registry is not None:
            return self._registry
        if not self._registry_path.exists():
            self._registry = Registry(accounts={})
            return self._registry
        data = json.loads(self._registry_path.read_text(encoding="utf-8"))
        self._registry = Registry.from_dict(data)
        return self._registry

    def _save_registry(self, registry: Registry) -> None:
        """保存注册表。"""
        self._registry = registry
        self._registry_path.write_text(
            json.dumps(registry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_accounts(self) -> list[AccountMeta]:
        """列出所有账号。"""
        registry = self._load_registry()
        return list(registry.accounts.values())

    def get_account(self, account_id: str) -> AccountMeta | None:
        """获取单个账号。"""
        registry = self._load_registry()
        return registry.accounts.get(account_id)

    def get_active_account(self) -> AccountMeta | None:
        """获取当前活跃账号。"""
        registry = self._load_registry()
        if registry.active_account_id is None:
            return None
        return registry.accounts.get(registry.active_account_id)

    def get_active_auth_path(self) -> Path | None:
        """获取当前活跃账号的 auth.json 路径。"""
        account = self.get_active_account()
        if account is None:
            return None
        return self._accounts_dir / account.id / "auth.json"

    def set_active_account(self, account_id: str) -> AccountMeta | None:
        """设置活跃账号，返回账号元数据或 None（如果不存在）。"""
        registry = self._load_registry()
        if account_id not in registry.accounts:
            return None
        registry.active_account_id = account_id
        now = datetime.now(timezone.utc).isoformat()
        registry.accounts[account_id].last_used = now
        self._save_registry(registry)
        return registry.accounts[account_id]

    def save_account(
        self,
        name: str,
        email: str | None,
        storage_state: dict[str, Any],
        account_id: str | None = None,
        auth_user: str = "0",
    ) -> AccountMeta:
        """保存新账号。"""
        registry = self._load_registry()
        now = datetime.now(timezone.utc).isoformat()
        created_at = now
        if account_id is None and email:
            for acc in registry.accounts.values():
                if acc.email == email and acc.auth_user == auth_user:
                    account_id = acc.id
                    created_at = acc.created_at
                    break

        if account_id is None:
            account_id = _generate_account_id()

        meta = AccountMeta(
            id=account_id,
            name=name,
            email=email,
            created_at=created_at,
            last_used=now,
            auth_user=auth_user,
        )
        account_dir = self._accounts_dir / account_id
        account_dir.mkdir(parents=True, exist_ok=True)
        # 写入 auth.json
        (account_dir / "auth.json").write_text(
            json.dumps(storage_state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 写入 meta.json
        (account_dir / "meta.json").write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 更新注册表
        registry.accounts[account_id] = meta
        if registry.active_account_id is None:
            registry.active_account_id = account_id
        self._save_registry(registry)
        return meta

    def delete_account(self, account_id: str) -> bool:
        """删除账号，返回是否成功。"""
        registry = self._load_registry()
        if account_id not in registry.accounts:
            return False
        # 删除目录
        account_dir = self._accounts_dir / account_id
        if account_dir.is_dir():
            shutil.rmtree(account_dir)
        # 从注册表移除
        del registry.accounts[account_id]
        if registry.active_account_id == account_id:
            registry.active_account_id = next(iter(registry.accounts), None)
        self._save_registry(registry)
        return True

    def update_account(self, account_id: str, name: str) -> AccountMeta | None:
        """更新账号名称。"""
        registry = self._load_registry()
        if account_id not in registry.accounts:
            return None
        registry.accounts[account_id].name = name
        # 同步更新 meta.json
        account_dir = self._accounts_dir / account_id
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            meta_path.write_text(
                json.dumps(registry.accounts[account_id].to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self._save_registry(registry)
        return registry.accounts[account_id]

    def get_auth_path(self, account_id: str) -> Path | None:
        """获取指定账号的 auth.json 路径。"""
        return self.get_auth_path_optional(account_id, require_exists=True)

    def get_auth_path_optional(self, account_id: str, *, require_exists: bool = False) -> Path | None:
        """获取指定账号的 auth.json 路径，可选是否要求文件已存在。"""
        registry = self._load_registry()
        if account_id not in registry.accounts:
            return None
        path = self._accounts_dir / account_id / "auth.json"
        if require_exists and not path.exists():
            return None
        return path

    def get_profile_path(self, account_id: str) -> Path | None:
        """获取指定账号的 profile 目录路径。"""
        registry = self._load_registry()
        if account_id not in registry.accounts:
            return None
        return self._accounts_dir / account_id / "profile"
