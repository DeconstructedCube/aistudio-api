"""Gateway session management for authentication and request signing."""

from __future__ import annotations

import asyncio
import inspect
import itertools
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from aistudio_api.config import settings
from aistudio_api.domain.errors import StaleSessionError
from aistudio_api.infrastructure.account.cookie_parser import (
    ROTATING_SESSION_COOKIE_NAMES,
    build_makersuite_headers,
    get_auth_user_from_meta,
    get_cookie_id_from_meta,
    get_tier_from_meta,
    load_cookies_from_auth_file,
    parse_raw_cookies,
)
from aistudio_api.infrastructure.gateway.headers import (
    merge_header_case_insensitive,
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
    cookie_id: str = ""
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
        self._signing_locks: dict[str, asyncio.Lock] = {}
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

    def is_context_current(self, context: SessionContext) -> bool:
        """凭据代际变化后，旧快照不得再次进入签名服务或回写。"""
        return not context.auth_file or (
            get_cookie_id_from_meta(context.auth_file) == context.cookie_id
        )

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
            cookie_id=get_cookie_id_from_meta(auth_file),
        )
        self._active_context = new_context
        log.info(
            "会话已切换: auth_file=%s, u/%s, tier=%s",
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
        force_recreate: bool = False,
    ) -> str:
        """同一账号串行签名，防止迟到的旧凭据重建并回滚新 Worker。"""
        active_ctx = ctx or await self.ensure_ready()
        key = active_ctx.auth_file or f"auth-user:{active_ctx.auth_user}"
        lock = self._signing_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not self.is_context_current(active_ctx):
                raise StaleSessionError("签名快照已被新导入凭据替换")
            try:
                proof = await self._generate_snapshot_current(
                    contents, active_ctx, model, force_recreate=force_recreate
                )
            except Exception as exc:
                if not self.is_context_current(active_ctx):
                    raise StaleSessionError("旧代际签名失败，凭据已更新") from exc
                raise
            if not self.is_context_current(active_ctx):
                raise StaleSessionError("签名期间凭据已更新，请重新准备请求")
            return proof

    async def _generate_snapshot_current(
        self,
        contents: list[AistudioContent],
        active_ctx: SessionContext,
        model: str,
        force_recreate: bool = False,
    ) -> str:
        """仅供已持有账号签名锁的当前代际调用。"""
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

        if not self.is_context_current(active_ctx):
            raise StaleSessionError("签名前凭据代际已更新")
        if active_ctx.auth_file:
            disk_cookies = load_cookies_from_auth_file(active_ctx.auth_file)
            if disk_cookies:
                active_ctx.cookies.update(disk_cookies)
        cookies = active_ctx.cookies
        auth_user = active_ctx.auth_user or "0"
        tier = active_ctx.tier or "Free"

        if force_recreate:
            proof = await self._signer.generate_snapshot(
                contents=contents,
                cookies=cookies,
                auth_user=auth_user,
                tier=tier,
                model=model,
                force_recreate=True,
            )
        else:
            proof = await self._signer.generate_snapshot(
                contents=contents,
                cookies=cookies,
                auth_user=auth_user,
                tier=tier,
                model=model,
            )
        self._sync_signer_cookies(active_ctx)
        return proof

    def _sync_signer_cookies(self, active_ctx: SessionContext) -> None:
        """仅当 WAA 签名服务冷启动 (cold) 访问官网产生新 SIDCC 时同步回本地；严禁热复用 (hot) 用旧 PSIDTS 反向覆盖。"""
        if not self.is_context_current(active_ctx):
            return
        pool_state = getattr(self._signer, "last_pool_state", None)
        if pool_state == "hot":
            return
        dyn_headers = getattr(self._signer, "last_headers", None)
        if not isinstance(dyn_headers, dict):
            return
        raw_cookie = dyn_headers.get("Cookie") or dyn_headers.get("cookie")
        if not raw_cookie or not isinstance(raw_cookie, str):
            return
        parsed = parse_raw_cookies(raw_cookie)
        if not parsed:
            return
        sync_names = (
            ("SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC")
            if pool_state == "cold"
            else ROTATING_SESSION_COOKIE_NAMES
        )
        changed = False
        for k in sync_names:
            val = parsed.get(k)
            if val and active_ctx.cookies.get(k) != val:
                active_ctx.cookies[k] = val
                changed = True
        if changed and active_ctx.auth_file:
            try:
                from aistudio_api.infrastructure.account.account_store import (
                    AccountStore,
                )

                AccountStore().update_account_cookies(
                    auth_file=active_ctx.auth_file,
                    new_cookies=active_ctx.cookies,
                    expected_cookie_id=active_ctx.cookie_id,
                )
            except Exception as e:
                log.debug("回写签名服务 Cookie 失败: %s", e)

    async def generate_snapshot_with_headers(
        self,
        contents: list[AistudioContent],
        ctx: SessionContext | None = None,
        model: str = "models/gemini-3.7-flash",
        force_recreate: bool = False,
    ) -> tuple[str, dict[str, str]]:
        """Generate fresh WAA proof token and dynamic session headers."""
        proof = await self.generate_snapshot(
            contents=contents,
            ctx=ctx,
            model=model,
            force_recreate=force_recreate,
        )
        return proof, dict(self.last_signer_headers)

    @staticmethod
    def build_request_headers(
        headers: dict[str, str] | None, auth_user: str
    ) -> dict[str, str]:
        """合并默认上游头、鉴权用户与调用方覆盖头，全程大小写不敏感去重。"""
        merged: dict[str, str] = {}
        for name, value in DEFAULT_UPSTREAM_HEADERS.items():
            merge_header_case_insensitive(merged, str(name), str(value))
        merge_header_case_insensitive(merged, "X-Goog-AuthUser", str(auth_user or "0"))
        for name, value in (headers or {}).items():
            merge_header_case_insensitive(merged, str(name), str(value))
        return merged

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
        req_headers = self.build_request_headers(
            headers, auth_user=active_ctx.auth_user
        )
        return await self._transport.send_request(
            url=target_url,
            headers=req_headers,
            body=body,
            auth_user=active_ctx.auth_user,
            tier=active_ctx.tier,
            auth_file=active_ctx.auth_file,
            cookies=active_ctx.cookies,
            expected_cookie_id=active_ctx.cookie_id,
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
        req_headers = self.build_request_headers(
            headers, auth_user=active_ctx.auth_user
        )
        async for event in self._transport.send_streaming_request(
            url=target_url,
            headers=req_headers,
            body=body,
            auth_user=active_ctx.auth_user,
            tier=active_ctx.tier,
            auth_file=active_ctx.auth_file,
            cookies=active_ctx.cookies,
            expected_cookie_id=active_ctx.cookie_id,
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
