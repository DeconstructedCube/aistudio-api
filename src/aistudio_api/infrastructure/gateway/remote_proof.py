"""Remote WAA / BotGuard proof minting client using reverse-engineered API."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.gateway.wire_types import AistudioContent

logger = get_logger("remote_proof")


class RemoteProofError(RuntimeError):
    """Raised when remote WAA proof minting fails."""


def _format_cookies(cookies: Any) -> str:
    """Normalize cookies into a standard Cookie header string."""
    if isinstance(cookies, str):
        return cookies.strip()
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


def _serialize_contents_to_messages(
    contents: list[AistudioContent] | list[dict[str, Any]] | str | None,
) -> list[dict[str, str]]:
    """Convert AistudioContent items or prompt into simple messages format for proof digest."""
    if not contents:
        return [{"role": "user", "content": "hi"}]
    if isinstance(contents, str):
        return [{"role": "user", "content": contents}]

    messages: list[dict[str, str]] = []
    for item in contents:
        role = "user"
        text_parts: list[str] = []
        if hasattr(item, "role") and hasattr(item, "parts"):
            role = item.role or "user"
            for p in item.parts:
                if getattr(p, "text", None):
                    text_parts.append(str(p.text))
                elif getattr(p, "file_id", None):
                    text_parts.append(str(p.file_id))
        elif isinstance(item, dict):
            role = str(item.get("role") or "user")
            parts = item.get("parts")
            if isinstance(parts, list):
                for p in parts:
                    if isinstance(p, dict) and p.get("text"):
                        text_parts.append(str(p["text"]))
                    elif isinstance(p, str):
                        text_parts.append(p)
            elif item.get("content"):
                text_parts.append(str(item["content"]))

        combined = " ".join(text_parts).strip() or "hi"
        messages.append({"role": role, "content": combined})

    return messages or [{"role": "user", "content": "hi"}]


class RemoteProofClient:
    """Async client for minting BotGuard/WAA !proof signatures via remote API."""

    def __init__(self, default_url: str = "https://waa.spinsnow.fun", timeout: float = 15.0) -> None:
        self._default_url = default_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def mint_proof(
        self,
        *,
        cookies: Any,
        model: str = "models/gemini-3.8-flash",
        auth_user: int | str = 0,
        contents: list[AistudioContent] | list[dict[str, Any]] | str | None = None,
        remote_url: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Mint a BotGuard !proof signature from remote API."""
        cookie_header = _format_cookies(cookies)
        if not cookie_header:
            raise RemoteProofError("Cannot mint proof: cookies are empty")

        url_base = (remote_url or self._default_url).rstrip("/")
        endpoint = f"{url_base}/v1/proof/mint"

        try:
            auth_u = int(auth_user)
        except (TypeError, ValueError):
            auth_u = 0

        messages = _serialize_contents_to_messages(contents)

        payload = {
            "cookies": cookie_header,
            "authUser": auth_u,
            "model": model or "models/gemini-3.8-flash",
            "messages": messages,
        }

        client = await self._get_client()
        t0 = time.perf_counter()

        try:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout or self._timeout,
            )
        except Exception as e:
            raise RemoteProofError(f"Network error connecting to proof server {endpoint}: {e}") from e

        latency_ms = int((time.perf_counter() - t0) * 1000)

        if resp.status_code != 200:
            raise RemoteProofError(
                f"Proof server returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            data = resp.json()
        except Exception as e:
            raise RemoteProofError(f"Failed to parse proof server JSON response: {e}") from e

        if not isinstance(data, dict) or not data.get("ok"):
            err_msg = data.get("error") if isinstance(data, dict) else "unknown"
            raise RemoteProofError(f"Proof server mint error: {err_msg}")

        proof = data.get("proof")
        if not proof or not isinstance(proof, str):
            raise RemoteProofError("Proof server returned empty proof string")

        pool_state = data.get("poolState", "unknown")
        logger.info(
            "🚀 逆向 WAA 签名成功: model=%s, state=%s, 耗时 %d ms (proof 长度 %d)",
            model,
            pool_state,
            latency_ms,
            len(proof),
        )
        return proof
