"""Gateway session management for authentication and request signing."""

from __future__ import annotations

import inspect
import itertools
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from aistudio_api.config import settings
from aistudio_api.infrastructure.account.cookie_parser import (
    build_makersuite_headers,
    get_auth_user_from_meta,
    get_tier_from_meta,
    load_cookies_from_auth_file,
)
from aistudio_api.infrastructure.gateway.transport import HTTPStreamTransport
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger
from aistudio_api.infrastructure.waa.signer import WaaSigner

log = get_logger("session")


@dataclass
class SessionContext:
    """Encapsulates active authentication context and metadata for AI Studio gateway."""

    auth_user: str = "0"
    auth_file: str | None = None
    tier: str = "Free"
    cookies: dict[str, str] = field(default_factory=dict)
    context_id: int = field(default_factory=itertools.count(1).__next__)
    created_at: float = field(default_factory=time.time)

    def is_usable(self) -> bool:
        return bool(self.auth_file or self.cookies)


GENERATE_CONTENT_URL = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent"

DEFAULT_UPSTREAM_HEADERS: dict[str, str] = build_makersuite_headers()


class GatewaySession:
    """Manages authentication state, WAA request signing, and transport routing."""

    def __init__(self):
        self._auth_file: str | None = settings.auth_file or None
        self._auth_user: str | None = None
        self._tier: str | None = None
        self._active_context: SessionContext | None = None
        self._transport = HTTPStreamTransport(proxy_url=settings.proxy_url)
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_active_waa_server,
        )

        active_waa = get_active_waa_server()
        server_url = str(active_waa.get("url") or settings.proof_server_url)
        self._signer = WaaSigner(
            proof_server_url=server_url,
            proxy_url=settings.proxy_url,
        )

    @property
    def is_ready(self) -> bool:
        return self._active_context is not None and self._active_context.is_usable()

    @property
    def active_context(self) -> SessionContext | None:
        return self._active_context

    @property
    def last_signer_headers(self) -> dict[str, str]:
        """Get dynamic protocol headers returned by the underlying signer."""
        return self._signer.last_headers

    def set_ready_context(self, context: SessionContext) -> None:
        """Set active ready context (used by transitions and test fixtures)."""
        self._auth_file = context.auth_file
        self._auth_user = context.auth_user
        self._tier = context.tier
        self._active_context = context

    def get_current_auth_user(self) -> str:
        """Get auth_user index of active account."""
        if self._auth_user is not None:
            return self._auth_user
        if self._active_context is not None:
            return self._active_context.auth_user
        return get_auth_user_from_meta(self._auth_file)

    def get_current_tier(self) -> str:
        """Get benefit tier of active account."""
        if self._active_context is not None and self._active_context.tier:
            return self._active_context.tier
        if self._tier is not None:
            return self._tier
        return get_tier_from_meta(self._auth_file)

    async def ensure_ready(self) -> SessionContext:
        """Ensure session context is initialized and ready."""
        if self.is_ready and self._active_context is not None:
            return self._active_context
        return await self.transition_to_account(self._auth_file, force_refresh=False)

    async def switch_auth(self, auth_file: str | None) -> None:
        """Atomic account switch."""
        await self.transition_to_account(auth_file, force_refresh=True)

    async def transition_to_account(
        self, auth_file: str | None, force_refresh: bool = False
    ) -> SessionContext:
        """Execute instantaneous in-memory session transition."""
        if (
            not force_refresh
            and self.is_ready
            and self._active_context is not None
            and self._active_context.auth_file == auth_file
        ):
            return self._active_context

        target_auth_user = get_auth_user_from_meta(auth_file)
        target_tier = get_tier_from_meta(auth_file)
        cookies = load_cookies_from_auth_file(auth_file)

        self._auth_file = auth_file
        self._auth_user = target_auth_user
        self._tier = target_tier

        new_context = SessionContext(
            auth_user=target_auth_user,
            auth_file=auth_file,
            tier=target_tier,
            cookies=cookies,
        )
        self._active_context = new_context
        log.info(
            "Session transitioned successfully: auth_file=%s, auth_user=%s, tier=%s",
            auth_file,
            target_auth_user,
            target_tier,
        )
        return new_context

    async def generate_snapshot(
        self,
        contents: list[AistudioContent],
        ctx: SessionContext | None = None,
        model: str = "models/gemini-3.7-flash",
    ) -> str:
        """Generate fresh WAA proof token for request contents."""
        active_ctx = ctx or await self.ensure_ready()
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_active_waa_server,
        )

        active_waa = get_active_waa_server()
        target_url = str(active_waa.get("url") or "").rstrip("/")
        remote = getattr(self._signer, "_remote_signer", None)
        if remote and target_url and getattr(remote, "server_url", None) != target_url:
            close_fn = getattr(self._signer, "close", None)
            if callable(close_fn):
                res = close_fn()
                if inspect.isawaitable(res):
                    await res
            remote.server_url = target_url

        cookies = active_ctx.cookies or load_cookies_from_auth_file(
            active_ctx.auth_file
        )
        auth_user = active_ctx.auth_user or "0"
        tier = active_ctx.tier or "Free"

        return await self._signer.generate_snapshot(
            contents=contents,
            cookies=cookies,
            auth_user=auth_user,
            tier=tier,
            model=model,
        )

    async def generate_snapshot_with_headers(
        self,
        contents: list[AistudioContent],
        ctx: SessionContext | None = None,
        model: str = "models/gemini-3.7-flash",
    ) -> tuple[str, dict[str, str]]:
        """Generate fresh WAA proof token and dynamic session headers."""
        active_ctx = ctx or await self.ensure_ready()
        cookies = active_ctx.cookies or load_cookies_from_auth_file(
            active_ctx.auth_file
        )
        auth_user = active_ctx.auth_user or "0"
        tier = active_ctx.tier or "Free"

        return await self._signer.generate_snapshot_with_headers(
            contents=contents,
            cookies=cookies,
            auth_user=auth_user,
            tier=tier,
            model=model,
        )

    async def send_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        ctx: object | None = None,
    ) -> tuple[int, bytes]:
        active_ctx = (
            ctx if isinstance(ctx, SessionContext) else await self.ensure_ready()
        )
        target_url = url or GENERATE_CONTENT_URL
        req_headers = dict(DEFAULT_UPSTREAM_HEADERS)
        req_headers["X-Goog-AuthUser"] = active_ctx.auth_user
        if headers:
            req_headers.update(headers)
        return await self._transport.send_request(
            url=target_url,
            headers=req_headers,
            body=body,
            auth_user=active_ctx.auth_user,
            tier=active_ctx.tier,
            auth_file=active_ctx.auth_file,
            cookies=active_ctx.cookies,
        )

    async def send_streaming_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        ctx: object | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        active_ctx = (
            ctx if isinstance(ctx, SessionContext) else await self.ensure_ready()
        )
        target_url = url or GENERATE_CONTENT_URL
        req_headers = dict(DEFAULT_UPSTREAM_HEADERS)
        req_headers["X-Goog-AuthUser"] = active_ctx.auth_user
        if headers:
            req_headers.update(headers)
        async for event in self._transport.send_streaming_request(
            url=target_url,
            headers=req_headers,
            body=body,
            auth_user=active_ctx.auth_user,
            tier=active_ctx.tier,
            auth_file=active_ctx.auth_file,
            cookies=active_ctx.cookies,
        ):
            yield event

    async def close(self) -> None:
        """Close transport, signer, and reset active context."""
        await self._transport.close()
        await self._signer.close()
        self._active_context = None


__all__ = [
    "DEFAULT_UPSTREAM_HEADERS",
    "GENERATE_CONTENT_URL",
    "GatewaySession",
    "SessionContext",
]
