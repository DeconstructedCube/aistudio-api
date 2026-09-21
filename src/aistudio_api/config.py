"""Runtime configuration helpers."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# 加载 .env 文件（如果存在）
load_dotenv()

DEFAULT_TEXT_MODEL = os.getenv("AISTUDIO_DEFAULT_TEXT_MODEL", "gemini-3.7-flash")
DEFAULT_IMAGE_MODEL = os.getenv(
    "AISTUDIO_DEFAULT_IMAGE_MODEL", "gemini-3.1-flash-image-preview"
)
DEFAULT_BROWSER_PORT = 9222


def _load_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return None


def _load_bool_env(*names: str, default: bool) -> bool:
    value = _load_env(*names)
    if value is None:
        return default
    return value not in ("0", "false", "False")


def _load_int_env(*names: str, default: int) -> int:
    value = _load_env(*names)
    if value is None:
        return default
    return int(value)


def _parse_api_keys(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()

    keys: list[str] = []
    for line in raw.splitlines():
        for part in line.split(","):
            key = part.strip()
            if key and key not in keys:
                keys.append(key)
    return tuple(keys)


def _load_web_password() -> str | None:
    return os.getenv("AISTUDIO_WEB_PASSWORD") or os.getenv("AISTUDIO_ADMIN_PASSWORD")


_AUTH_SEARCH_ROOTS = [
    Path(__file__).resolve().parents[2] / "data",  # 项目内 data/ 目录
]


def discover_auth_file() -> str | None:
    override = os.getenv("AISTUDIO_AUTH_FILE")
    if override:
        return override

    for root in _AUTH_SEARCH_ROOTS:
        if not root.is_dir():
            continue
        # 优先从 registry.json 读取活跃账号
        registry_path = root / "accounts" / "registry.json"
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text())
                active_id = registry.get("active_account_id")
                if active_id:
                    auth_path = root / "accounts" / active_id / "auth.json"
                    if auth_path.exists():
                        return str(auth_path)
            except (json.JSONDecodeError, KeyError):
                pass
        # 回退：扫描 data/ 目录下的 .json 文件
        for file in root.iterdir():
            if file.suffix == ".json":
                return str(file)
    return None


def discover_proxy_url() -> str | None:
    env_proxy = (
        os.getenv("AISTUDIO_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
    )
    if env_proxy:
        return env_proxy
    # 尝试自动探测本地代理（如 Termux 下的 Clash / v2ray）
    import socket

    for port in (7890, 10808):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.05)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return f"http://127.0.0.1:{port}"
        except Exception:
            pass
    return None


def build_browser_proxy(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return None

    proxy: dict[str, str] = {
        "server": f"{parsed.scheme}://{parsed.hostname}",
    }
    if parsed.port:
        proxy["server"] += f":{parsed.port}"
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


@dataclass(slots=True)
class Settings:
    port: int = int(os.getenv("AISTUDIO_PORT", "8080"))
    browser_port: int = _load_int_env(
        "AISTUDIO_BROWSER_PORT", default=DEFAULT_BROWSER_PORT
    )
    browser_headless: bool = _load_bool_env("AISTUDIO_BROWSER_HEADLESS", default=True)
    browser_executable_path: str | None = os.getenv("AISTUDIO_BROWSER_EXECUTABLE")
    browser_idle_timeout: int = int(os.getenv("AISTUDIO_BROWSER_IDLE_TIMEOUT", "0"))
    auth_file: str | None = discover_auth_file()
    tmp_dir: str = os.getenv("AISTUDIO_TMP_DIR", tempfile.gettempdir())
    proxy_url: str | None = discover_proxy_url()
    web_password: str | None = _load_web_password()
    api_keys: frozenset[str] = frozenset()
    timeout_replay: int = int(os.getenv("AISTUDIO_TIMEOUT_REPLAY", "120"))
    timeout_stream: int = int(os.getenv("AISTUDIO_TIMEOUT_STREAM", "120"))
    timeout_capture: int = int(os.getenv("AISTUDIO_TIMEOUT_CAPTURE", "30"))
    snapshot_cache_ttl: int = int(os.getenv("AISTUDIO_SNAPSHOT_CACHE_TTL", "3600"))
    snapshot_cache_max: int = int(os.getenv("AISTUDIO_SNAPSHOT_CACHE_MAX", "100"))
    dump_raw_response: bool = os.getenv("AISTUDIO_DUMP_RAW_RESPONSE", "0") in (
        "1",
        "true",
        "True",
    )
    dump_raw_response_dir: str = os.getenv(
        "AISTUDIO_DUMP_RAW_RESPONSE_DIR", tempfile.gettempdir()
    )
    accounts_dir: str = os.getenv("AISTUDIO_ACCOUNTS_DIR", "")
    account_max_retries: int = int(os.getenv("AISTUDIO_ACCOUNT_MAX_RETRIES", "3"))

    @property
    def auth_enabled(self) -> bool:
        """网页管理端鉴权是否开启。"""
        return bool(self.web_password)


settings = Settings()
