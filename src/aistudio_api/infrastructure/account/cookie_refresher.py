"""Cookie loading for browser injection via raw cookie string + curl_cffi refresh."""

from __future__ import annotations

import time

from aistudio_api.infrastructure.account.cookie_parser import (
    DISCARDABLE_IMPORT_COOKIE_NAMES,
)
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("cookie_refresher")

# Keep browser injection behavior close to the original implementation that was
# known to produce a working login session after the browser visited Google.
AUTH_COOKIE_NAMES = {
    "SID",
    "SSID",
    "HSID",
    "APISID",
    "SAPISID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
}


def _should_skip_browser_injection(name: str) -> bool:
    """Skip cookies that CDP rejects or that pollute fresh session negotiation.

    1. __Host-* cookies must be excluded because attaching domain causes CDP rejection.
    2. Transient/telemetry/stale challenge cookies are skipped so the browser can negotiate
       fresh origin-bound tokens under the current network and TLS fingerprint.
    """
    if name.startswith("__Host-"):
        return True
    return name in DISCARDABLE_IMPORT_COOKIE_NAMES or name.startswith(("_ga_", "_gcl_"))


def _parse_cookie_string(raw: str) -> dict[str, str]:
    """Parse a semicolon-separated cookie string into a dict."""
    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def _refresh_session_cookies(cookies: dict[str, str]) -> dict[str, str]:
    """Use curl_cffi to hit Google login flow and refresh session cookies."""
    try:
        from curl_cffi import requests
    except ImportError:
        log.warning("未安装 curl_cffi，保持原始 Cookie 返回")
        return dict(cookies)
    from aistudio_api.config import settings

    session = (
        requests.Session(proxy=settings.proxy_url)
        if settings.proxy_url
        else requests.Session()
    )
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".google.com")

    try:
        resp = session.get(
            "https://accounts.google.com/ServiceLogin?continue=https://aistudio.google.com",
            impersonate="chrome",
            timeout=15,
            allow_redirects=True,
        )
        log.debug("Google ServiceLogin 探活响应: HTTP %d", resp.status_code)
    except Exception as e:
        log.warning("刷新会话 Cookie 失败: %s", e)
        return dict(cookies)

    all_cookies = dict(session.cookies)
    log.info("会话 Cookie 刷新完成: 共 %d 个", len(all_cookies))
    return all_cookies


def load_cookies_from_string(cookie_string: str) -> list[dict[str, object]]:
    """Load cookies from a raw cookie string.

    Parses directly, refreshes session cookies via curl_cffi,
    returns Playwright-format cookies for browser injection.
    Real expires come from browser export after visiting the page.
    """
    parsed = _parse_cookie_string(cookie_string)
    refreshed = _refresh_session_cookies(parsed)
    merged = dict(parsed)
    merged.update(refreshed)
    default_expires = int(time.time()) + 86400 * 180  # 180 天后过期

    cookies = []
    skipped_names: list[str] = []
    for name, value in merged.items():
        if _should_skip_browser_injection(name):
            skipped_names.append(name)
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": name not in AUTH_COOKIE_NAMES,
                "sameSite": "None",
                "expires": default_expires,
            }
        )
    log.info(
        "Cookie 合并完成: 原始=%d, 刷新=%d, 合并后=%d",
        len(parsed),
        len(refreshed),
        len(merged),
    )
    if skipped_names:
        log.info(
            "跳过 Host-only Cookie 注入: %s",
            sorted(skipped_names),
        )
    log.info("Cookie 解析完成，共 %d 个有效凭据", len(cookies))
    return cookies
