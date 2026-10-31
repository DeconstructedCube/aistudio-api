"""Direct HTTP streaming and non-streaming transport layer.

Provides high-performance, browserless HTTP communication with Google AI Studio
using connection pooling and chunk streaming.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from pathlib import Path

import httpx

from aistudio_api.config import settings
from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.account.cookie_parser import (
    calculate_sapisid_hash,
    get_tier_header_value,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("transport")


def _get_auth_cookies(auth_file: str | None = None) -> dict[str, str] | None:
    """Read credentials from the bound auth_file or active account."""
    try:
        if auth_file:
            bound_path = Path(auth_file)
            if bound_path.is_file():
                data = json.loads(bound_path.read_text(encoding="utf-8"))
                cookies = data.get("cookies") or []
                if isinstance(cookies, list):
                    return {
                        str(c.get("name") or ""): str(c.get("value") or "")
                        for c in cookies
                        if isinstance(c, dict) and c.get("name")
                    }
                if isinstance(cookies, dict):
                    return {str(k): str(v) for k, v in cookies.items() if k}
        from aistudio_api.infrastructure.account.account_store import AccountStore

        store = AccountStore()
        active = store.get_active_account()
        if not active:
            return None
        auth_path = store.get_auth_path_optional(active.id, require_exists=True)
        if auth_path and auth_path.is_file():
            data = json.loads(auth_path.read_text(encoding="utf-8"))
            cookies = data.get("cookies") or []
            if isinstance(cookies, list):
                return {
                    str(c.get("name") or ""): str(c.get("value") or "")
                    for c in cookies
                    if isinstance(c, dict) and c.get("name")
                }
            if isinstance(cookies, dict):
                return {str(k): str(v) for k, v in cookies.items() if k}
    except Exception:
        pass
    return None


def _get_active_account_tier() -> str:
    """Read active account's subscription tier."""
    try:
        from aistudio_api.infrastructure.account.account_store import AccountStore

        store = AccountStore()
        active = store.get_active_account()
        if active and getattr(active, "tier", None):
            return str(active.tier)
    except Exception:
        pass
    return "Free"


async def _ensure_authorization_header(
    headers: dict[str, str],
    auth_user: str = "0",
    tier: str | None = None,
    auth_file: str | None = None,
) -> dict[str, str]:
    """Ensure SAPISIDHASH Authorization, X-Goog-AuthUser, and X-AIStudio-G1-Tier are set."""
    incoming_auth = ""
    for k, v in headers.items():
        if k.lower() == "authorization" and v:
            incoming_auth = str(v)
            break

    clean_headers = {
        k: v
        for k, v in headers.items()
        if k.lower()
        not in (
            "host",
            "content-length",
            "authorization",
            "x-goog-authuser",
            "x-aistudio-g1-tier",
        )
    }
    clean_headers["X-Goog-AuthUser"] = str(auth_user or "0")
    effective_tier = tier if tier and tier != "Free" else _get_active_account_tier()
    tier_header = get_tier_header_value(effective_tier)
    if tier_header:
        clean_headers["X-AIStudio-G1-Tier"] = tier_header

    cookie_dict = _get_auth_cookies(auth_file)

    if cookie_dict:
        fresh_auth = calculate_sapisid_hash(cookie_dict)
        if fresh_auth:
            clean_headers["Authorization"] = fresh_auth
        if "Cookie" not in clean_headers:
            clean_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_dict.items() if v)
    elif incoming_auth:
        clean_headers["Authorization"] = incoming_auth

    return clean_headers


class HTTPStreamTransport:
    """Direct HTTP streaming and non-streaming transport layer."""

    def __init__(self, proxy_url: str | None = None, timeout: float = 120.0) -> None:
        self.proxy_url = proxy_url or settings.proxy_url
        self.timeout = timeout
        self._queues: dict[str, asyncio.Queue[dict[str, object]]] = {}
        self._client: httpx.AsyncClient | None = None

    @property
    def in_flight_count(self) -> int:
        return len(self._queues)

    def abort_stream(self, rid: str, reason: str = "stream_aborted") -> bool:
        q = self._queues.get(rid)
        if q is not None:
            try:
                q.put_nowait({"type": "error", "message": reason})
                return True
            except asyncio.QueueFull:
                return False
        return False

    def close(self, reason: str = "transport_closed") -> None:
        for q in list(self._queues.values()):
            with suppress(Exception):
                q.put_nowait({"type": "error", "message": reason})
        self._queues.clear()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                proxy=self.proxy_url,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._client

    async def send_hooked_request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
        auth_user: str = "0",
        tier: str | None = None,
        auth_file: str | None = None,
    ) -> tuple[int, bytes]:
        """Execute non-streaming HTTP request directly."""
        t0 = time.perf_counter()
        clean_headers = await _ensure_authorization_header(
            headers, auth_user=auth_user, tier=tier, auth_file=auth_file
        )
        logger.debug(
            "Sending HTTP POST: url=%s, body_len=%d, auth_user=%s",
            url,
            len(body),
            auth_user,
        )

        client = await self._get_client()
        try:
            resp = await client.post(url, headers=clean_headers, content=body.encode("utf-8"))
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.debug(
                "HTTP POST completed: status=%d, time=%.1fms, resp_len=%d",
                resp.status_code,
                elapsed_ms,
                len(resp.content),
            )
            return resp.status_code, resp.content
        except Exception as exc:
            logger.error("HTTP request failed: url=%s, err=%s", url, exc)
            raise BrowserTransportError(f"HTTP request failed: {exc}") from exc

    async def send_streaming_request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
        auth_user: str = "0",
        tier: str | None = None,
        auth_file: str | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Stream HTTP response chunks directly."""
        clean_headers = await _ensure_authorization_header(
            headers, auth_user=auth_user, tier=tier, auth_file=auth_file
        )
        rid = uuid.uuid4().hex[:8]
        logger.debug("Starting streaming HTTP request: rid=%s, url=%s", rid, url)

        client = await self._get_client()
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._queues[rid] = queue

        try:
            req = client.build_request("POST", url, headers=clean_headers, content=body.encode("utf-8"))
            resp = await client.send(req, stream=True)
            yield ("status", resp.status_code)

            if resp.status_code != 200:
                err_body = await resp.aread()
                yield ("chunk", err_body)
                return

            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield ("chunk", chunk)

        except Exception as exc:
            logger.warning("Streaming request encountered exception: %s", exc)
            yield ("error", str(exc))
        finally:
            self._queues.pop(rid, None)


