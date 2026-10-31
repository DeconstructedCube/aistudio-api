"""Authentication, cookies, and identity verification helpers for AI Studio sessions."""

from __future__ import annotations

import json
from pathlib import Path

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


async def extract_account_email(auth_file: str | None = None) -> str | None:
    """Extract authenticated Google email from metadata."""
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
    auth_user: str = "0",
    cookies: dict[str, str] | None = None,
    proxy_url: str | None = None,
) -> str | None:
    """Extract authenticated Google AI benefit tier via GetAiStudioBenefitTier RPC."""
    cookie_dict = cookies or {}
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
