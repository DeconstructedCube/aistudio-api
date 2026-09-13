"""Cookie loading for browser injection via raw cookie string + curl_cffi refresh."""

from __future__ import annotations

import logging
import time

log = logging.getLogger("aistudio.cookie_refresher")

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
    """Skip cookies that CDP rejects when we force a Domain attribute.

    We intentionally keep the broad ``.google.com`` injection strategy because it
    was the last known-good login path. The only cookies we must exclude are
    ``__Host-*`` cookies, which are required to be host-only and therefore cause
    ``Storage.setCookies: Invalid cookie fields`` if we attach ``domain``.
    """
    return name.startswith("__Host-")


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
        log.warning("curl_cffi not installed, returning cookies as-is")
        return dict(cookies)

    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".google.com")

    try:
        resp = session.get(
            "https://accounts.google.com/ServiceLogin?continue=https://aistudio.google.com",
            impersonate="chrome",
            timeout=15,
            allow_redirects=True,
        )
        log.debug("GET ServiceLogin: %d", resp.status_code)
    except Exception as e:
        log.warning("Failed to refresh session cookies: %s", e)
        return dict(cookies)

    all_cookies = dict(session.cookies)
    log.info("Refreshed cookies: %d total", len(all_cookies))
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
        "[cookie_string] raw=%d refreshed=%d merged=%d",
        len(parsed),
        len(refreshed),
        len(merged),
    )
    if skipped_names:
        log.info(
            "[cookie_string] skipped host-only cookies for browser injection: %s",
            sorted(skipped_names),
        )
    log.info("[cookie_string] parsed %d cookies", len(cookies))
    return cookies
