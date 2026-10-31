"""Authentication, cookies, and identity verification helpers for AI Studio sessions."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aistudio_api.infrastructure.account.cookie_parser import (
    DEFAULT_API_KEY,
    DEFAULT_EXT_BIN,
    DEFAULT_ORIGIN,
    DEFAULT_USER_AGENT,
    calculate_sapisid_hash,
    normalize_benefit_tier,
    parse_benefit_tier_response,
)
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session.auth")

AI_STUDIO_URL = (
    "https://aistudio.google.com/prompts/new_chat?model=gemini-3.7-flash&temporary=true"
)
AI_STUDIO_URL_FALLBACK = (
    "https://aistudio.google.com/app/prompts/new_chat?temporary=true"
)
GOOGLE_LOGIN_BOOTSTRAP_URL = (
    "https://accounts.google.com/ServiceLogin?continue=https://aistudio.google.com"
)


def is_login_page_url(url: str | None) -> bool:
    """True if URL matches Google account signin / login paths."""
    if not url:
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
    except Exception:
        return False
    if host == "accounts.google.com" or host.endswith(".accounts.google.com"):
        return True
    return (
        "signin" in path
        or "servicelogin" in path
        or "accountchooser" in path
        or "signin" in query
        or "servicelogin" in query
    )


def is_aistudio_page_url(url: str | None) -> bool:
    """True if URL points to aistudio.google.com and is not a Google login page."""
    if not url or is_login_page_url(url):
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
        return host == "aistudio.google.com" or host.endswith(".aistudio.google.com")
    except Exception:
        return False


def derive_profile_dir(auth_file: str | None) -> str | None:
    """Derive user data profile directory corresponding to an auth file."""
    if not auth_file:
        return None
    return str(Path(auth_file).resolve().parent / "profile")


def get_auth_user_from_meta(auth_file: str | None) -> str:
    """Read auth_user index from meta.json adjacent to auth_file."""
    if auth_file:
        try:
            meta_path = Path(auth_file).parent / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return str(meta.get("auth_user") or "0")
        except Exception:
            pass
    return "0"


def get_tier_from_meta(auth_file: str | None) -> str:
    """Read benefit tier from meta.json adjacent to auth_file."""
    if auth_file:
        try:
            meta_path = Path(auth_file).parent / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return normalize_benefit_tier(str(meta.get("tier") or "Free"))
        except Exception:
            pass
    return "Free"


def get_aistudio_urls(
    auth_user: str = "0", model: str = "gemini-3.7-flash"
) -> list[str]:
    """Build AI Studio navigation URLs for target auth_user."""
    if auth_user and auth_user != "0":
        return [
            f"https://aistudio.google.com/u/{auth_user}/prompts/new_chat?model={model}&temporary=true",
            f"https://aistudio.google.com/u/{auth_user}/app/prompts/new_chat?temporary=true",
        ]
    return [AI_STUDIO_URL, AI_STUDIO_URL_FALLBACK]


def load_cookies_from_auth_file(auth_file: str | None) -> dict[str, str]:
    """Read cookies from auth_file into name-value dictionary."""
    if not auth_file or not Path(auth_file).is_file():
        return {}
    try:
        data = json.loads(Path(auth_file).read_text(encoding="utf-8"))
        cookies = data.get("cookies") or []
        if isinstance(cookies, list):
            return {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in cookies
                if isinstance(c, dict) and c.get("name")
            }
        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items() if k}
    except Exception as e:
        log.warning("Failed to load cookies from %s: %s", auth_file, e)
    return {}


async def load_cookies_to_page(page: object, auth_file: str | None) -> bool:
    """Compatibility shim for injecting cookies into page if page exists."""
    if not auth_file or not Path(auth_file).is_file():
        return False
    if hasattr(page, "account_auth_file"):
        setattr(page, "account_auth_file", auth_file)
    cookies_dict = load_cookies_from_auth_file(auth_file)
    set_cookies_fn = getattr(page, "set_cookies", None)
    if callable(set_cookies_fn):
        with suppress(Exception):
            cookie_list = [{"name": k, "value": v} for k, v in cookies_dict.items()]
            res = set_cookies_fn(cookie_list)
            if asyncio.iscoroutine(res):
                await res
    return bool(cookies_dict)


async def extract_account_email(_page: object | None, auth_file: str | None = None) -> str | None:
    """Extract authenticated Google email from metadata or page."""
    if auth_file:
        try:
            meta_path = Path(auth_file).parent / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                email = meta.get("email")
                if email and isinstance(email, str) and "@" in email:
                    return email.strip()
        except Exception:
            pass
    return None


async def extract_account_tier(
    page: object | None = None,
    auth_user: str = "0",
    cookies: dict[str, str] | None = None,
    proxy_url: str | None = None,
) -> str | None:
    """Extract authenticated Google AI benefit tier via GetAiStudioBenefitTier RPC."""
    cookie_dict = cookies or {}
    if not cookie_dict and page is not None:
        cached = getattr(page, "_cached_cookies", None)
        if isinstance(cached, dict):
            cookie_dict = cached

    if not cookie_dict:
        return "Free"

    auth_header = calculate_sapisid_hash(cookie_dict, origin=DEFAULT_ORIGIN)
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items() if v)

    headers = {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": DEFAULT_USER_AGENT,
        "X-Goog-Api-Key": DEFAULT_API_KEY,
        "X-Goog-AuthUser": str(auth_user or "0"),
        "X-Goog-Ext-519733851-bin": DEFAULT_EXT_BIN,
        "Origin": DEFAULT_ORIGIN,
        "Referer": f"{DEFAULT_ORIGIN}/",
        "Cookie": cookie_header,
    }
    if auth_header:
        headers["Authorization"] = auth_header

    url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetAiStudioBenefitTier"
    try:
        async with httpx.AsyncClient(timeout=10.0, proxy=proxy_url) as client:
            resp = await client.post(url, headers=headers, content="[]")
            if resp.status_code == 200:
                return parse_benefit_tier_response(resp.text)
    except Exception as e:
        log.debug("GetAiStudioBenefitTier request failed: %s", e)
    return None


async def save_browser_cookies(page: object, auth_file: str | None) -> None:
    """Save updated cookies to auth_file if available."""
    if not auth_file:
        return
    get_cookies_fn = getattr(page, "get_cookies", None)
    if callable(get_cookies_fn):
        try:
            res = get_cookies_fn()
            cookies = await res if asyncio.iscoroutine(res) else res
            if cookies:
                Path(auth_file).write_text(json.dumps({"cookies": cookies}, indent=2), encoding="utf-8")
        except Exception:
            pass


async def navigate_to_aistudio(
    page: object,
    auth_user: str = "0",
    _auth_file: str | None = None,
) -> None:
    """Compatibility stub for page navigation."""
    goto_fn = getattr(page, "goto", None)
    if callable(goto_fn):
        with suppress(Exception):
            urls = get_aistudio_urls(auth_user=auth_user)
            res = goto_fn(urls[0])
            if asyncio.iscoroutine(res):
                await res


async def bootstrap_google_session(
    page: object,
    auth_file: str | None,
    auth_user: str = "0",
) -> None:
    """Compatibility stub for google session bootstrap."""
