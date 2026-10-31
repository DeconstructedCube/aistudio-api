"""WAA proof signing services.

Supports:
1. RemoteProofSigner: mints proof via remote proof server (e.g. spinsnow or self-hosted relay).
2. WaaSigner: Unified signer facade providing get_snapshot / generate_proof.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import httpx

from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.account.cookie_parser import (
    get_tier_header_value,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("waa.signer")

DEFAULT_PROOF_SERVER_URL = "https://waa.spinsnow.fun"


def extract_content_digest(contents: list[AistudioContent]) -> tuple[str, str]:
    """Strictly extract contents for SHA-256 digest calculation.

    Follows Google AI Studio frontend logic (live_m=_b.js: _.gD / _.jn / _.VC):
    1. Only extracts contents (field 2), never includes system_instruction
    2. Parts: text -> getText(), inline_data -> data.Fe(), file -> getId()
    3. Joined by single space " " and hashed with sha256.
    Returns (prompt_string, sha256_hexdigest).
    """
    hash_parts: list[str] = []
    for content in contents:
        for part in content.parts:
            if part.text is not None:
                hash_parts.append(str(part.text))
            elif part.inline_data is not None:
                hash_parts.append(str(part.inline_data[1]))
            elif part.file_id is not None:
                hash_parts.append(str(part.file_id))
            else:
                hash_parts.append("")
    prompt = " ".join(hash_parts)
    content_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, content_hash


class RemoteProofSigner:
    """Signs requests using external WAA proof server (spinsnow / self-hosted)."""

    def __init__(
        self,
        server_url: str = DEFAULT_PROOF_SERVER_URL,
        proxy_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.proxy_url = proxy_url
        self.timeout = timeout
        self._lock = asyncio.Lock()

    async def check_health(self, cookies: dict[str, str], auth_user: str = "0") -> bool:
        """Check if account is warm / ready on proof server."""
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
        headers = {
            "X-Google-Cookies": cookie_header,
            "X-Goog-AuthUser": str(auth_user),
        }
        try:
            async with httpx.AsyncClient(timeout=5.0, proxy=self.proxy_url) as client:
                res = await client.get(f"{self.server_url}/health", headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    return bool(data.get("ok"))
        except Exception as e:
            logger.debug("Proof server health check failed: %s", e)
        return False

    async def mint_proof(
        self,
        *,
        cookies: dict[str, str],
        auth_user: str = "0",
        tier: str | None = None,
        model: str = "models/gemini-3.7-flash",
        prompt: str = "",
        wire_contents: list[Any] | None = None,
        _content_hash: str = "",
    ) -> str:
        """Request proof from remote proof server."""
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
        if not cookie_header:
            raise ValueError("Cannot mint proof: cookies are empty")

        g1_tier = get_tier_header_value(tier) or "TIER0"
        normalized_model = model if model.startswith("models/") else f"models/{model}"

        payload: dict[str, Any] = {
            "cookies": cookie_header,
            "authUser": int(auth_user) if auth_user.isdigit() else 0,
            "g1Tier": g1_tier,
            "model": normalized_model,
            "prompt": prompt,
        }
        if wire_contents is not None:
            payload["wireContents"] = wire_contents

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy_url) as client:
            resp = await client.post(
                f"{self.server_url}/v1/proof/mint",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code != 200:
            raise BrowserTransportError(
                f"Remote proof server returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        if not data.get("ok") or not data.get("proof"):
            err_msg = data.get("error") or "unknown proof server error"
            raise BrowserTransportError(f"Remote proof minting failed: {err_msg}")

        proof = str(data["proof"])
        pool_state = str(data.get("poolState") or "unknown")
        logger.debug(
            "Proof generated successfully: poolState=%s, len=%d, time=%.1fms",
            pool_state,
            len(proof),
            elapsed_ms,
        )
        return proof


class WaaSigner:
    """Unified WAA signing facade with automatic signer resolution."""

    def __init__(
        self,
        proof_server_url: str | None = None,
        proxy_url: str | None = None,
    ) -> None:
        url = proof_server_url or DEFAULT_PROOF_SERVER_URL
        self._remote_signer = RemoteProofSigner(server_url=url, proxy_url=proxy_url)
        self._cache: dict[str, tuple[str, float]] = {}  # hash -> (proof, timestamp)
        self._cache_ttl = 60.0  # seconds

    async def generate_snapshot(
        self,
        contents: list[AistudioContent],
        cookies: dict[str, str],
        auth_user: str = "0",
        tier: str = "Free",
        model: str = "models/gemini-3.7-flash",
    ) -> str:
        """Compute content hash and generate fresh WAA proof token."""
        prompt, content_hash = extract_content_digest(contents)
        wire_contents = [c.to_wire() for c in contents]
        logger.debug(
            "Generating WAA snapshot: hash=%s, prompt_len=%d, auth_user=%s",
            content_hash[:8],
            len(prompt),
            auth_user,
        )

        return await self._remote_signer.mint_proof(
            cookies=cookies,
            auth_user=auth_user,
            tier=tier,
            model=model,
            prompt=prompt,
            wire_contents=wire_contents,
            _content_hash=content_hash,
        )
