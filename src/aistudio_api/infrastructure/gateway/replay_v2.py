"""Request replay service — supports both browser proxy and pure HTTP."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.capture import CapturedRequest

logger = logging.getLogger("aistudio")


def compute_sapisidhash(sapisid: str, origin: str = "https://aistudio.google.com") -> str:
    """Compute SAPISIDHASH from SAPISID cookie."""
    ts = int(time.time())
    hash_input = f"{ts} {sapisid} {origin}"
    hash_val = hashlib.sha1(hash_input.encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{hash_val} SAPISID1PHASH {ts}_{hash_val} SAPISID3PHASH {ts}_{hash_val}"


class RequestReplayService:
    def __init__(self, session=None):
        self._session = session

    async def replay(
        self,
        captured: CapturedRequest | None,
        body: str,
        timeout: int | None = None,
        api_key: Optional[str] = None,
        visit_id: Optional[str] = None,
    ) -> tuple[int, bytes]:
        if not captured:
            return 0, b""

        if timeout is None:
            timeout = settings.timeout_replay

        # Try browser proxy first (most reliable)
        if self._session:
            return await self._replay_via_browser(captured, body, timeout)

        # Fallback: pure HTTP (requires fresh SAPISIDHASH)
        return await self._replay_via_http(captured, body, timeout, api_key, visit_id)

    async def _replay_via_browser(
        self,
        captured: CapturedRequest,
        body: str,
        timeout: int,
    ) -> tuple[int, bytes]:
        """Replay via browser proxy (handles TLS session binding)."""
        target = await self._session.ensure_context()
        headers = {k: v for k, v in captured.headers.items() if k.lower() not in ("host", "content-length")}

        try:
            if hasattr(target, "pages"):
                pages = target.pages
                page = pages[0] if pages else await target.new_page()
            else:
                page = target

            # Make request via page.evaluate XHR (browser handles cookies)
            result = await page.evaluate("""(args) => {
                return new Promise((resolve) => {
                    var xhr = new XMLHttpRequest();
                    xhr.open('POST', args.url);
                    var h = args.headers;
                    for (var k in h) {
                        xhr.setRequestHeader(k, h[k]);
                    }
                    xhr.withCredentials = true;
                    xhr.onload = function() {
                        resolve({status: xhr.status, body: xhr.responseText});
                    };
                    xhr.onerror = function() {
                        resolve({status: 0, body: 'network error'});
                    };
                    xhr.send(args.body);
                });
            }""", {
                "url": captured.url,
                "headers": headers,
                "body": body,
            })

            status = result.get("status", 0)
            response_body = result.get("body", "").encode("utf-8")
            return status, response_body

        except Exception as exc:
            logger.error("Browser replay error: %s", exc)
            return 0, str(exc).encode()

    async def _replay_via_http(
        self,
        captured: CapturedRequest,
        body: str,
        timeout: int,
        api_key: Optional[str] = None,
        visit_id: Optional[str] = None,
    ) -> tuple[int, bytes]:
        """Replay via pure HTTP (requires fresh SAPISIDHASH)."""
        import aiohttp

        headers = {k: v for k, v in captured.headers.items() if k.lower() not in ("host", "content-length")}

        # Add API key and visit ID if provided
        if api_key:
            headers["X-Goog-Api-Key"] = api_key
        if visit_id:
            headers["X-AIStudio-Visit-Id"] = visit_id

        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.post(
                    captured.url,
                    data=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    raw = await resp.read()
                    return resp.status, raw
        except Exception as exc:
            logger.error("HTTP replay error: %s", exc)
            return 0, str(exc).encode()
