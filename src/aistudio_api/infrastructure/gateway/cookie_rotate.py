"""Lightweight Google session cookie rotation via accounts.google.com/RotateCookies."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from aistudio_api.infrastructure.utils.common import atomic_write_json
from aistudio_api.infrastructure.gateway.session_auth import sync_cookies_to_sibling_accounts
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage

logger = get_logger("cookie_rotate")

ROTATE_URL = "https://accounts.google.com/RotateCookies"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def parse_set_cookie_header(header: str) -> dict[str, Any]:
    """Parse a Set-Cookie header into a CDP-compatible cookie dictionary."""
    parts = [p.strip() for p in header.split(";")]
    if not parts or "=" not in parts[0]:
        return {}

    name, val = parts[0].split("=", 1)
    name = name.strip()
    val = val.strip()
    if not name or not val or val == "deleted":
        return {}

    now = int(time.time())
    cookie: dict[str, Any] = {
        "name": name,
        "value": val,
        "domain": ".google.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "expires": now + 86400 * 30,
    }

    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            kl = k.strip().lower()
            vl = v.strip()
            if kl == "domain":
                cookie["domain"] = vl
            elif kl == "path":
                cookie["path"] = vl
            elif kl == "samesite":
                cookie["sameSite"] = vl
            elif kl == "max-age":
                try:
                    cookie["expires"] = now + int(vl)
                except ValueError:
                    pass
        else:
            kl = part.strip().lower()
            if kl == "httponly":
                cookie["httpOnly"] = True
            elif kl == "secure":
                cookie["secure"] = True

    return cookie


def build_cookie_header_string(cookies: list[dict[str, Any]] | dict[str, str]) -> str:
    """Build a Cookie header string (k=v; k=v) from cookie collection."""
    if isinstance(cookies, dict):
        return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    if isinstance(cookies, list):
        pairs: list[str] = []
        for c in cookies:
            if isinstance(c, dict):
                name = c.get("name")
                val = c.get("value")
                if name and val:
                    pairs.append(f"{name}={val}")
        return "; ".join(pairs)
    return ""


async def rotate_google_cookies_http(
    cookies: list[dict[str, Any]] | dict[str, str],
    *,
    timeout_s: float = 15.0,
    user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[bool, list[dict[str, Any]], str | None]:
    """Execute a pure HTTP POST request to accounts.google.com/RotateCookies.

    Returns:
        (success, updated_cookies_list, error_or_none)
    """
    cookie_str = build_cookie_header_string(cookies)
    if not cookie_str:
        return False, [], "Missing cookies"

    headers = {
        "Cookie": cookie_str,
        "Content-Type": "application/json",
        "Origin": "https://accounts.google.com",
        "Referer": "https://accounts.google.com/RotateCookiesPage",
        "User-Agent": user_agent,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                ROTATE_URL,
                headers=headers,
                json=[None, "-1234567890", 0],
            )
    except Exception as e:
        return False, [], f"Network error during RotateCookies: {e}"

    if resp.status_code != 200:
        return False, [], f"RotateCookies returned HTTP {resp.status_code}: {resp.text[:120]}"

    set_cookie_headers = resp.headers.get_list("set-cookie")
    if not set_cookie_headers:
        return False, [], "RotateCookies returned HTTP 200 but no Set-Cookie headers"

    new_cookies: list[dict[str, Any]] = []
    for sc in set_cookie_headers:
        parsed = parse_set_cookie_header(sc)
        if parsed and parsed.get("name"):
            new_cookies.append(parsed)

    if not new_cookies:
        return False, [], "Failed to parse any cookies from Set-Cookie headers"

    return True, new_cookies, None


def merge_rotated_cookies(
    original_cookies: list[dict[str, Any]],
    new_cookies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge newly rotated cookies into the original cookies list."""
    updated_map: dict[str, dict[str, Any]] = {c["name"]: dict(c) for c in new_cookies}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for old in original_cookies:
        name = old.get("name")
        if not name:
            continue
        if name in updated_map:
            merged.append(updated_map[name])
            seen.add(name)
        else:
            merged.append(dict(old))
            seen.add(name)

    # Append any brand new cookies
    for new in new_cookies:
        name = new.get("name")
        if name and name not in seen:
            merged.append(new)
            seen.add(name)

    return merged


async def rotate_account_cookies(
    auth_file: str | Path,
    page: CDPPage | None = None,
    timeout_s: float = 15.0,
) -> bool:
    """Rotate an account's cookies via HTTP, save to disk, and broadcast to sibling accounts.

    Also updates the active CDP page cookie jar if provided.
    """
    path = Path(auth_file)
    if not path.is_file():
        logger.warning("RotateCookies: auth_file不存在: %s", auth_file)
        return False

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("RotateCookies: 解析 %s 失败: %s", auth_file, e)
        return False

    raw_cookies = data.get("cookies")
    if not isinstance(raw_cookies, list) or not raw_cookies:
        logger.warning("RotateCookies: %s 内无有效 cookies 列表", auth_file)
        return False

    t0 = time.perf_counter()
    ok, new_cookies, err = await rotate_google_cookies_http(raw_cookies, timeout_s=timeout_s)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    if not ok:
        logger.warning("RotateCookies: 轮转失败 (耗时 %d ms): %s", latency_ms, err)
        return False

    merged = merge_rotated_cookies(raw_cookies, new_cookies)
    data["cookies"] = merged
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 1. Save to auth_file
    atomic_write_json(path, data)

    # 2. Sync to all sibling accounts sharing the same cookie_id
    synced_count = sync_cookies_to_sibling_accounts(path, data)

    # 3. Update live CDP browser page cookies if available
    if page and not page.is_closed():
        try:
            await page.set_cookies(new_cookies)
        except Exception as e:
            logger.debug("向 CDP Page 同步新 Cookie 失败: %s", e)

    refreshed_names = [c["name"] for c in new_cookies]
    logger.info(
        "🚀 RotateCookies 成功刷新: %s (耗时 %d ms, 同步 %d 个兄弟账号)",
        ", ".join(refreshed_names),
        latency_ms,
        synced_count,
    )
    return True
