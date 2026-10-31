"""Gateway session management for authentication and request signing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.session_auth import (
    extract_account_email,
    extract_account_tier,
    get_auth_user_from_meta,
    get_tier_from_meta,
    load_cookies_from_auth_file,
)
from aistudio_api.infrastructure.gateway.session_state import (
    SessionContext,
    SessionReadinessGate,
    SessionState,
)
from aistudio_api.infrastructure.gateway.transport import HTTPStreamTransport
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger
from aistudio_api.infrastructure.waa.signer import WaaSigner

log = get_logger("session")


DEFAULT_BOOTSTRAP_TEMPLATE: dict[str, object] = {
    "url": "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent",
    "headers": {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "X-Goog-Api-Key": "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs",
        "X-Goog-AuthUser": "0",
        "X-Goog-Ext-519733851-bin": "CAASAUIwATgEQABQBFgDYgJKUHAAeAGQAQCYAQE=",
        "Origin": "https://aistudio.google.com",
        "Referer": "https://aistudio.google.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    },
    "body": '["models/gemini-3.7-flash"]',
}


class GatewaySession:
    """Manages authentication state, WAA request signing, and transport routing."""

    def __init__(self):
        self._auth_file: str | None = settings.auth_file or None
        self._auth_user: str | None = None
        self._tier: str | None = None

        self._gate = SessionReadinessGate()
        self._transition_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._transport = HTTPStreamTransport(proxy_url=settings.proxy_url)
        self._signer = WaaSigner(
            proof_server_url=getattr(settings, "proof_server_url", None),
            proxy_url=settings.proxy_url,
        )
        self._in_flight: int = 0

    @property
    def state(self) -> SessionState:
        return self._gate.state

    @property
    def is_ready(self) -> bool:
        return self._gate.is_ready()

    @property
    def active_context(self) -> SessionContext | None:
        return self._gate.active_context

    @property
    def snap_key(self) -> str | None:
        return self._gate.active_context.snap_key if self._gate.active_context else "waa_proof"

    @property
    def base_template(self) -> dict[str, object] | None:
        return (
            self._gate.active_context.base_template
            if self._gate.active_context
            else DEFAULT_BOOTSTRAP_TEMPLATE
        )

    def set_ready_context(self, context: SessionContext) -> None:
        """Set active ready context (used by transitions and test fixtures)."""
        self._auth_file = context.auth_file
        self._auth_user = context.auth_user
        self._tier = context.tier
        self._gate.set_ready(context)

    def get_current_auth_user(self) -> str:
        """Get auth_user index of active account."""
        if self._auth_user is not None:
            return self._auth_user
        if self._gate.active_context is not None:
            return self._gate.active_context.auth_user
        return get_auth_user_from_meta(self._auth_file)

    def get_current_tier(self) -> str:
        """Get benefit tier of active account."""
        if self._gate.active_context is not None and self._gate.active_context.tier:
            return self._gate.active_context.tier
        if self._tier is not None:
            return self._tier
        return get_tier_from_meta(self._auth_file)

    async def extract_account_email(self) -> str | None:
        """Extract email for current active account."""
        return await extract_account_email(auth_file=self._auth_file)

    async def extract_account_tier(self) -> str | None:
        """Extract subscription tier for current active account."""
        cookies = (
            self._gate.active_context.cookies
            if self._gate.active_context
            else load_cookies_from_auth_file(self._auth_file)
        )
        auth_user = self.get_current_auth_user()
        return await extract_account_tier(
            cookies=cookies, auth_user=auth_user, proxy_url=settings.proxy_url
        )

    @asynccontextmanager
    async def request_scope(self):
        """Track in-flight requests."""
        self._in_flight += 1
        try:
            yield self.active_context
        finally:
            self._in_flight = max(0, self._in_flight - 1)

    async def ensure_ready(self) -> SessionContext:
        """Wait until session context is ready."""
        if self._gate.is_ready() and self._gate.active_context is not None:
            return self._gate.active_context

        if self._gate.state == SessionState.UNINITIALIZED:
            await self.transition_to_account(self._auth_file, force_refresh=False)

        return await self._gate.wait_ready()


    async def is_alive(self) -> bool:
        """Check if session is operational."""
        return self._gate.is_ready()

    async def switch_auth(self, auth_file: str | None) -> None:
        """Atomic account switch."""
        await self.transition_to_account(auth_file, force_refresh=True)

    async def transition_to_account(
        self, auth_file: str | None, force_refresh: bool = False
    ) -> SessionContext:
        """Execute instantaneous in-memory session transition."""
        if (
            not force_refresh
            and self._gate.is_ready()
            and self._gate.active_context is not None
            and self._gate.active_context.auth_file == auth_file
        ):
            return self._gate.active_context

        self._gate.set_preparing()
        async with self._transition_lock:
            if (
                not force_refresh
                and self._gate.is_ready()
                and self._gate.active_context is not None
                and self._gate.active_context.auth_file == auth_file
            ):
                return self._gate.active_context

            self._gate.set_preparing()
            target_auth_user = get_auth_user_from_meta(auth_file)
            target_tier = get_tier_from_meta(auth_file)
            cookies = load_cookies_from_auth_file(auth_file)

            self._auth_file = auth_file
            self._auth_user = target_auth_user
            self._tier = target_tier

            base_template = dict(DEFAULT_BOOTSTRAP_TEMPLATE)
            raw_headers = base_template.get("headers")
            base_headers: dict[str, str] = (
                {str(k): str(v) for k, v in raw_headers.items()}
                if isinstance(raw_headers, dict)
                else {}
            )
            base_headers["X-Goog-AuthUser"] = target_auth_user
            base_template["headers"] = base_headers

            new_context = SessionContext(
                snap_key="waa_proof",
                auth_user=target_auth_user,
                auth_file=auth_file,
                base_template=base_template,
                tier=target_tier,
                cookies=cookies,
            )

            self._gate.set_ready(new_context)
            log.info(
                "Session transitioned successfully: auth_file=%s, auth_user=%s, tier=%s",
                auth_file,
                target_auth_user,
                target_tier,
            )
            return new_context

    async def capture_template(
        self, model: str, ctx: SessionContext | None = None
    ) -> dict[str, object]:
        active_ctx = ctx or self.active_context
        base = dict(
            active_ctx.base_template if active_ctx else DEFAULT_BOOTSTRAP_TEMPLATE
        )
        normalized_model = model if model.startswith("models/") else f"models/{model}"
        try:
            body_val = base.get("body")
            if (
                isinstance(body_val, str)
                and body_val.startswith('["models/')
                and "," in body_val
            ):
                first_comma = body_val.find(",")
                base["body"] = f'["{normalized_model}"' + body_val[first_comma:]
            elif isinstance(body_val, str) and body_val.startswith("["):
                body_list = json.loads(body_val)
                if isinstance(body_list, list) and len(body_list) > 0:
                    body_list[0] = normalized_model
                    base["body"] = json.dumps(body_list, separators=(",", ":"))
        except Exception as e:
            log.debug("Failed to adapt model name in template: %s", e)
        return base

    async def generate_snapshot(
        self,
        contents: list[AistudioContent],
        ctx: SessionContext | None = None,
        model: str = "models/gemini-3.7-flash",
    ) -> str:
        """Generate fresh WAA proof token for request contents."""
        active_ctx = ctx or await self.ensure_ready()
        cookies = active_ctx.cookies or load_cookies_from_auth_file(active_ctx.auth_file)
        auth_user = active_ctx.auth_user or "0"
        tier = active_ctx.tier or "Free"

        async with self._snapshot_lock:
            return await self._signer.generate_snapshot(
                contents=contents,
                cookies=cookies,
                auth_user=auth_user,
                tier=tier,
                model=model,
            )

    async def send_hooked_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        ctx: object | None = None,
    ) -> tuple[int, bytes]:
        ctx = ctx if isinstance(ctx, SessionContext) else await self.ensure_ready()
        captured_url = url or str(ctx.base_template.get("url") or "")
        hdrs_val = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in hdrs_val.items()}
            if isinstance(hdrs_val, dict)
            else {}
        )
        ctx.in_flight += 1
        try:
            return await self._transport.send_hooked_request(
                url=captured_url,
                headers=captured_headers,
                body=body,
                auth_user=ctx.auth_user,
                tier=ctx.tier,
                auth_file=ctx.auth_file,
            )
        finally:
            ctx.decr_in_flight()

    async def send_streaming_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        ctx: object | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        ctx = ctx if isinstance(ctx, SessionContext) else await self.ensure_ready()
        captured_url = url or str(ctx.base_template.get("url") or "")
        hdrs_val = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in hdrs_val.items()}
            if isinstance(hdrs_val, dict)
            else {}
        )
        ctx.in_flight += 1
        try:
            async for event in self._transport.send_streaming_request(
                url=captured_url,
                headers=captured_headers,
                body=body,
                auth_user=ctx.auth_user,
                tier=ctx.tier,
                auth_file=ctx.auth_file,
            ):
                yield event
        finally:
            ctx.decr_in_flight()

    async def import_cookies(
        self, cookie_string: str, auth_file: str | None = None
    ) -> int:
        from aistudio_api.infrastructure.account.cookie_parser import (
            parse_cookie_string,
        )

        state = parse_cookie_string(cookie_string, discard_transient=True)
        raw_cookies = state.get("cookies")
        pw_cookies: list[dict[str, object]] = (
            raw_cookies if isinstance(raw_cookies, list) else []  # type: ignore[assignment]
        )
        target_auth = auth_file or self._auth_file
        if not target_auth:
            return len(pw_cookies)
        cookies_dict = {
            str(c["name"]): str(c["value"])
            for c in pw_cookies
            if isinstance(c, dict) and c.get("name")
        }
        Path(target_auth).write_text(
            json.dumps({"cookies": pw_cookies}, indent=2), encoding="utf-8"
        )
        if self._gate.active_context and self._gate.active_context.auth_file == target_auth:
            self._gate.active_context.cookies = cookies_dict
        return len(pw_cookies)

    async def close(self) -> None:
        """Close transport, signer, and reset readiness."""
        self._transport.close()
        await self._signer.close()
        self._gate.reset()

__all__ = [
    "DEFAULT_BOOTSTRAP_TEMPLATE",
    "GatewaySession",
]
