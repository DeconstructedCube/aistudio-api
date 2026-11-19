"""Local WAA proof signing client over multiplexed stdio IPC."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    AccountRestrictedError,
    AistudioError,
    SessionExpiredError,
    TransportError,
    is_account_restricted_payload,
    is_login_redirect_payload,
)
from aistudio_api.infrastructure.account.cookie_parser import (
    filter_minimal_cookies,
    get_tier_header_value,
)
from aistudio_api.infrastructure.gateway.wire_types import (
    AistudioContent,
    _normalize_standard_base64,
)
from aistudio_api.infrastructure.utils.logger import get_logger
from aistudio_api.infrastructure.waa.daemon import (
    LocalSignerProcess,
    get_signer_process,
)

logger = get_logger("waa.signer")


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
            if part.file_id is not None:
                hash_parts.append(str(part.file_id))
            elif part.inline_data is not None:
                hash_parts.append(_normalize_standard_base64(str(part.inline_data[1])))
            elif (
                part.function_call is not None
                or part.function_response is not None
                or part.executable_code is not None
                or part.code_execution_result is not None
                or part.video_metadata is not None
            ):
                hash_parts.append("")
            elif part.text is not None:
                hash_parts.append(str(part.text))
            else:
                hash_parts.append("")
    prompt = " ".join(hash_parts)
    content_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, content_hash


class LocalIpcProofSigner:
    """Signs requests via the local SpiderMonkey 152 waa-signer stdio IPC child."""

    def __init__(
        self,
        process: LocalSignerProcess | None = None,
        timeout: float = 95.0,
    ) -> None:
        self._process = process or get_signer_process()
        self.timeout = timeout
        self.last_headers: dict[str, str] = {}
        self.last_pool_state: str = ""

    async def close(self) -> None:
        """Close local IPC signer resources if owned exclusively."""
        self.last_headers = {}

    async def mint_proof(
        self,
        *,
        cookies: dict[str, str],
        auth_user: str = "0",
        tier: str | None = None,
        model: str = "models/gemini-3.7-flash",
        prompt: str = "",
        wire_contents: list[Any] | None = None,
        force_recreate: bool = False,
    ) -> str:
        """Request WAA proof from local waa-signer over stdio IPC."""
        min_cookies = filter_minimal_cookies(cookies)
        cookie_header = "; ".join(f"{k}={v}" for k, v in min_cookies.items())
        if not cookie_header:
            raise ValueError("Cannot mint proof: cookies are empty")

        g1_tier = get_tier_header_value(tier) or "TIER0"
        normalized_model = model if model.startswith("models/") else f"models/{model}"

        payload: dict[str, object] = {
            "cookies": cookie_header,
            "authUser": int(auth_user) if auth_user.isdigit() else 0,
            "g1Tier": g1_tier,
            "model": normalized_model,
            "prompt": prompt,
        }
        if settings.proxy_url:
            payload["proxy"] = settings.proxy_url
        if wire_contents is not None:
            payload["wireContents"] = wire_contents
        if force_recreate:
            payload["forceRecreate"] = True

        max_attempts = 2
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                t0 = time.perf_counter()
                data = await self._process.request(
                    "proof.mint",
                    payload,
                    timeout=self.timeout,
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000

                if not data.get("ok") or not data.get("proof"):
                    err_msg = str(data.get("error") or "unknown local IPC signer error")
                    if (
                        "隔离登录态失效" in err_msg
                        or "accounts.google.com" in err_msg.lower()
                        or is_login_redirect_payload(err_msg)
                    ):
                        raise SessionExpiredError(f"WAA 签名检测登录态失效: {err_msg}")
                    if is_account_restricted_payload(err_msg):
                        raise AccountRestrictedError(
                            f"AI Studio 账号服务受限: {err_msg}"
                        )
                    raise TransportError(f"Local proof minting failed: {err_msg}")

                proof = str(data["proof"])
                raw_headers = data.get("headers")
                if isinstance(raw_headers, dict):
                    self.last_headers = {str(k): str(v) for k, v in raw_headers.items()}
                else:
                    self.last_headers = {}
                pool_state = str(data.get("poolState") or "unknown")
                self.last_pool_state = pool_state
                logger.debug(
                    "本地 IPC 生成 WAA 签名成功: pool=%s, len=%d, headers=%d, 耗时=%.1fms",
                    pool_state,
                    len(proof),
                    len(self.last_headers),
                    elapsed_ms,
                )
                return proof
            except (SessionExpiredError, AccountRestrictedError):
                raise
            except TransportError as exc:
                # Only retry transient IPC pipe/startup failures, not WAA logical errors
                if "Local proof minting failed:" in str(exc):
                    raise
                last_error = exc
                logger.warning(
                    "本地 IPC 签名通信失败 (%d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.2)
            except AistudioError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "请求本地 IPC 签名异常 (%d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.2)

        raise TransportError(
            f"Local proof minting failed after {max_attempts} attempts: {last_error}"
        )


class WaaSigner:
    """Unified WAA signing facade backed by LocalIpcProofSigner."""

    def __init__(self, process: LocalSignerProcess | None = None) -> None:
        self._ipc_signer = LocalIpcProofSigner(process=process)

    async def close(self) -> None:
        """Close underlying local IPC signer resources."""
        await self._ipc_signer.close()

    @property
    def last_headers(self) -> dict[str, str]:
        """Get dynamic protocol headers returned by the last successful proof minting."""
        return self._ipc_signer.last_headers

    @property
    def last_pool_state(self) -> str:
        """Get worker pool state ('cold' or 'hot') from the last proof minting."""
        return self._ipc_signer.last_pool_state

    async def generate_snapshot_with_headers(
        self,
        contents: list[AistudioContent],
        cookies: dict[str, str],
        auth_user: str = "0",
        tier: str = "Free",
        model: str = "models/gemini-3.7-flash",
        force_recreate: bool = False,
    ) -> tuple[str, dict[str, str]]:
        """Generate fresh WAA proof token along with dynamic session headers."""
        proof = await self.generate_snapshot(
            contents=contents,
            cookies=cookies,
            auth_user=auth_user,
            tier=tier,
            model=model,
            force_recreate=force_recreate,
        )
        return proof, dict(self._ipc_signer.last_headers)

    async def generate_snapshot(
        self,
        contents: list[AistudioContent],
        cookies: dict[str, str],
        auth_user: str = "0",
        tier: str = "Free",
        model: str = "models/gemini-3.7-flash",
        force_recreate: bool = False,
    ) -> str:
        """Compute content hash and generate fresh WAA proof token."""
        prompt, content_hash = extract_content_digest(contents)
        wire_contents = [c.to_wire() for c in contents]
        logger.debug(
            "计算请求摘要: hash=%s, prompt_len=%d, u/%s",
            content_hash[:8],
            len(prompt),
            auth_user,
        )

        return await self._ipc_signer.mint_proof(
            cookies=cookies,
            auth_user=auth_user,
            tier=tier,
            model=model,
            prompt=prompt,
            wire_contents=wire_contents,
            force_recreate=force_recreate,
        )
