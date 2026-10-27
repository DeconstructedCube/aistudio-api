"""Shared browser session management for gateway operations."""

from __future__ import annotations

import asyncio
import gc
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.infrastructure.browser.browser_engine import (
    ChromiumProcess,
    launch_chromium_process,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    DOM_GC_CLEANUP_JS,
    SNAPSHOT_GENERATE_JS,
    build_snapshot_args,
)
from aistudio_api.infrastructure.gateway.botguard_bootstrap import (
    bootstrap_botguard_and_template,
)
from aistudio_api.infrastructure.gateway.session_auth import (
    bootstrap_google_session,
    derive_profile_dir,
    extract_account_email,
    extract_account_tier,
    get_auth_user_from_meta,
    get_tier_from_meta,
    is_aistudio_page_url,
    is_login_page_url,
    load_cookies_to_page,
    navigate_to_aistudio,
    save_browser_cookies,
)
from aistudio_api.infrastructure.gateway.session_state import (
    SessionContext,
    SessionReadinessGate,
    SessionState,
)
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session")

_is_login_page_url = is_login_page_url
_is_aistudio_page_url = is_aistudio_page_url

DEFAULT_BOOTSTRAP_TEMPLATE: dict[str, object] = {
    "url": "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent",
    "headers": {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "X-Goog-Api-Key": "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs",
        "X-Goog-AuthUser": "0",
        "Referer": "https://aistudio.google.com/",
    },
    "body": '["models/gemini-3.7-flash"]',
}


class BrowserSession:
    """Coordinates browser lifecycle, readiness gate, and blue-green tab transitions."""

    def __init__(self, port: int):
        self.port = port
        self._auth_file: str | None = settings.auth_file or None
        self._auth_user: str | None = None
        self._tier: str | None = None
        self._profile_dir = derive_profile_dir(self._auth_file)
        self._proc: ChromiumProcess | None = None
        self._cdp_client: CDPClient | None = None

        self._gate = SessionReadinessGate()
        self._transition_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._transport = XHRStreamTransport()
        self._retiring_contexts: list[SessionContext] = []
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._in_flight: int = 0
        self._stream_cleanup_count: int = 0

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
    def page(self) -> CDPPage | None:
        return self._gate.active_context.page if self._gate.active_context else None

    @property
    def snap_key(self) -> str | None:
        return self._gate.active_context.snap_key if self._gate.active_context else None

    @property
    def base_template(self) -> dict[str, object] | None:
        return (
            self._gate.active_context.base_template
            if self._gate.active_context
            else None
        )

    def set_ready_context(self, context: SessionContext) -> None:
        """Set active ready context (used by transitions and test fixtures)."""
        self._auth_file = context.auth_file
        self._gate.set_ready(context)

    def get_current_auth_user(self) -> str:
        """获取当前活跃账号的 auth_user 编号（0, 1, 2...）。"""
        if self._auth_user is not None:
            return self._auth_user
        if self._gate.active_context is not None:
            return self._gate.active_context.auth_user
        return get_auth_user_from_meta(self._auth_file)

    def get_current_tier(self) -> str:
        """获取当前活跃账号的订阅权益等级 (Free / Pro / Ultra / Plus)。"""
        if self._gate.active_context is not None and self._gate.active_context.tier:
            return self._gate.active_context.tier
        if self._tier is not None:
            return self._tier
        return get_tier_from_meta(self._auth_file)

    @asynccontextmanager
    async def request_scope(self):
        """追踪活动请求。"""
        self._in_flight += 1
        try:
            yield self.active_context
        finally:
            self._in_flight = max(0, self._in_flight - 1)

    async def ensure_ready(self) -> SessionContext:
        """Wait until session is 100% READY."""
        if self._gate.is_ready() and self._gate.active_context is not None:
            return self._gate.active_context

        if self._gate.state == SessionState.UNINITIALIZED:
            await self.transition_to_account(self._auth_file, force_refresh=False)

        return await self._gate.wait_ready()

    async def ensure_context(self) -> CDPPage:
        """Ensure active verified CDPPage is available, auto-reconnecting if dropped."""
        page = self.page
        if page is not None and not page.is_closed():
            if await page.is_alive(timeout_s=1.0):
                return page
            log.warning("当前页面无响应，尝试重新连接")
        await self._close_internal()
        res = await self._ensure_browser_cdp()
        return res or (await self.ensure_ready()).page

    async def is_alive(self, timeout_s: float = 1.5) -> bool:
        if self._proc is not None and not self._proc.is_alive():
            return False
        page = self.page
        if page is None or page.is_closed():
            return False
        return await page.is_alive(timeout_s=timeout_s)

    async def switch_auth(self, auth_file: str | None) -> None:
        """Switch auth using blue-green transition without killing in-flight streams."""
        await self.transition_to_account(auth_file, force_refresh=True)

    async def transition_to_account(
        self, auth_file: str | None, force_refresh: bool = False
    ) -> SessionContext:
        """Execute atomic session transition to target auth file."""
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
            old_context = self._gate.active_context
            target_auth_user = get_auth_user_from_meta(auth_file)
            target_tier = get_tier_from_meta(auth_file)
            self._auth_file = auth_file
            self._auth_user = target_auth_user
            self._tier = target_tier
            self._profile_dir = derive_profile_dir(auth_file)
            new_page: CDPPage | None = None
            created_dedicated_tab = False
            try:
                # Real browser mode
                if self._proc is None or not self._proc.is_alive():
                    await self._close_internal()
                    await self._ensure_browser_cdp()

                assert self._cdp_client is not None

                # Blue-Green: always create a dedicated new tab when transitioning from an existing account
                if old_context is not None:
                    new_page = await self._cdp_client.create_new_page(block_assets=True)
                    created_dedicated_tab = True
                else:
                    new_page = await self._cdp_client.connect_page(block_assets=True)

                await load_cookies_to_page(
                    new_page,
                    auth_file,
                    switching_account=old_context is not None,
                )
                await navigate_to_aistudio(
                    new_page,
                    auth_user=target_auth_user,
                    auth_file=auth_file,
                )

                async def _reload_cb(page: CDPPage) -> bool:
                    return await load_cookies_to_page(
                        page, auth_file, force_inject=True
                    )

                snap_key, base_template = await bootstrap_botguard_and_template(
                    new_page,
                    reload_cookies_cb=_reload_cb,
                    is_proc_alive_cb=lambda: (
                        self._proc is None or self._proc.is_alive()
                    ),
                    auth_user=target_auth_user,
                )

                new_page._cached_cookies = None
                await save_browser_cookies(new_page, auth_file)
                target_tier = get_tier_from_meta(auth_file)
                self._tier = target_tier
                new_context = SessionContext(
                    page=new_page,
                    snap_key=snap_key,
                    auth_user=target_auth_user,
                    auth_file=auth_file,
                    base_template=base_template,
                    tier=target_tier,
                )
                self._cdp_client.page = new_page

                if (
                    old_context is not None
                    and old_context.page.target_id != new_page.target_id
                    and not old_context.page.is_closed()
                ):
                    self._retiring_contexts.append(old_context)
                    drain_task = asyncio.create_task(
                        self._drain_retiring_context(
                            old_context, active_target_id=new_page.target_id
                        ),
                        name=f"drain_{old_context.auth_user}",
                    )
                    self._background_tasks.add(drain_task)
                    drain_task.add_done_callback(self._background_tasks.discard)
                self._gate.set_ready(new_context)
                log.info(
                    "会话切换成功: 文件=%s, auth_user=%s, 在途请求数=%d",
                    auth_file,
                    target_auth_user,
                    self._in_flight,
                )
                return new_context

            except Exception as exc:
                if new_page is not None and (
                    created_dedicated_tab
                    or old_context is None
                    or getattr(old_context.page, "target_id", None)
                    != getattr(new_page, "target_id", None)
                ):
                    with suppress(Exception):
                        if not new_page.is_closed():
                            await new_page.close()
                    if self._cdp_client is not None and getattr(
                        new_page, "target_id", None
                    ):
                        with suppress(Exception):
                            await self._cdp_client.close_target(new_page.target_id)
                self._gate.set_failed(exc)
                log.error("会话切换失败: 目标文件=%s, 错误=%s", auth_file, exc)
                raise

    async def _drain_retiring_context(
        self,
        context: SessionContext,
        active_target_id: str | None = None,
    ) -> None:
        while context.in_flight > 0:
            if context.page.is_closed():
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(context.drain_event.wait(), timeout=5.0)
        with suppress(Exception):
            if context in self._retiring_contexts:
                self._retiring_contexts.remove(context)
            if not context.page.is_closed():
                await context.page.close()
            if (
                self._cdp_client
                and context.page.target_id
                and context.page.target_id != active_target_id
            ):
                await self._cdp_client.close_target(context.page.target_id)

    async def ensure_botguard_service(self, force_refresh: bool = False) -> CDPPage:
        page = self.page
        curr_url = page.url if page else ""
        if "available-regions" in curr_url:
            raise RuntimeError(
                f"Google AI Studio 地区限制: 访问被重定向至 available-regions ({curr_url})"
            )
        if force_refresh or not self._gate.is_ready():
            await self.transition_to_account(
                self._auth_file, force_refresh=force_refresh
            )
        return (await self.ensure_ready()).page

    async def capture_template(
        self, model: str, ctx: SessionContext | None = None
    ) -> dict[str, object]:
        if self._proc is not None and not self._proc.is_alive():
            raise RuntimeError("Browser process died during template capture")

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
            log.debug("适配模型名称至模板失败: %s", e)
        return base

    async def generate_snapshot(
        self,
        contents: list[AistudioContent],
        ctx: SessionContext | None = None,
    ) -> str:
        active_ctx = ctx or await self.ensure_ready()
        # 严格遵守 Google AI Studio 前端 JS 逆向逻辑 (见 live_m=_b.js: _.gD / _.jn / _.VC):
        # 1. 仅提取 contents (字段 2)，绝不包含 system_instruction
        # 2. 遍历每个 Part: 文本 -> getText(), 内联图片 -> getData().Fe(), 文件 -> getId(), 其余 -> ""
        # 3. 数组所有元素以单空格 " " 拼接后计算 sha256(utf-8).hexdigest()
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
        content_hash = sha256(prompt.encode("utf-8")).hexdigest()
        log.debug("开始计算内容快照: hash=%s, prompt_len=%d", content_hash[:8], len(prompt))
        async with self._snapshot_lock:
            args = build_snapshot_args(prompt=prompt, content_hash=content_hash)
            for attempt in range(3):
                try:
                    raw_res = await active_ctx.page.evaluate(
                        SNAPSHOT_GENERATE_JS, args=args, timeout_s=15.0
                    )
                    snapshot = ""
                    if isinstance(raw_res, dict):
                        snapshot = str(raw_res.get("snapshot") or "")
                    elif isinstance(raw_res, str):
                        snapshot = raw_res
                    if snapshot:
                        log.debug("内容快照计算完成: hash=%s, 长度=%d", content_hash[:8], len(snapshot))
                        return snapshot
                except Exception as e:
                    log.debug("计算内容快照失败: 尝试=%d, 错误=%s", attempt + 1, e)
                if attempt < 2:
                    await active_ctx.page.wait_for_timeout(300)

        raise RuntimeError(
            f"Snapshot generation failed for content hash {content_hash[:8]}"
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
                ctx.page,
                url=captured_url,
                headers=captured_headers,
                body=body,
                auth_user=ctx.auth_user,
                tier=ctx.tier,
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
                ctx.page,
                url=captured_url,
                headers=captured_headers,
                body=body,
                auth_user=ctx.auth_user,
                tier=ctx.tier,
            ):
                yield event
        finally:
            ctx.decr_in_flight()
            await self.cleanup_stream_page(ctx.page)

    async def cleanup_stream_page(self, page: CDPPage | None = None) -> None:
        self._stream_cleanup_count += 1
        target_page = page or (
            self.active_context.page if self.active_context else None
        )
        if (
            target_page is not None
            and not target_page.is_closed()
            and self._in_flight <= 0
        ):
            with suppress(Exception):
                await target_page.evaluate(DOM_GC_CLEANUP_JS)
            if self._stream_cleanup_count % 5 == 0 and self._auth_file:
                # Google rolls __Secure-1PSIDTS inside the browser every ~15
                # minutes. Without this write-back a restart re-seeds the
                # session from hours-old credentials and immediately 403s.
                with suppress(Exception):
                    await save_browser_cookies(target_page, self._auth_file)
            if self._stream_cleanup_count % 10 == 0:
                with suppress(Exception):
                    await target_page.collect_garbage()
                gc.collect()

    async def import_cookies(
        self, cookie_string: str, auth_file: str | None = None
    ) -> int:
        from aistudio_api.infrastructure.account.cookie_refresher import (
            load_cookies_from_string,
        )

        pw_cookies = await asyncio.to_thread(load_cookies_from_string, cookie_string)
        target_auth = auth_file or self._auth_file
        if not target_auth:
            return len(pw_cookies)

        await self._ensure_browser_cdp()
        assert self._cdp_client is not None
        page = await self._cdp_client.create_new_page(block_assets=False)
        try:
            await page.set_cookies(pw_cookies)
            with suppress(Exception):
                await bootstrap_google_session(page)
            browser_cookies = await page.get_cookies()
            final_cookies = browser_cookies or pw_cookies
            await save_browser_cookies(
                page, auth_file=target_auth, cookies=final_cookies
            )
            return len(final_cookies)
        finally:
            with suppress(Exception):
                await page.close()
                await self._cdp_client.close_target(page.target_id)

    async def extract_account_email(self, page: CDPPage | None = None) -> str | None:
        target = page or (self.active_context.page if self.active_context else None)
        return await extract_account_email(target)

    async def extract_account_tier(self, page: CDPPage | None = None) -> str | None:
        target = page or (self.active_context.page if self.active_context else None)
        auth_user = self.get_current_auth_user()
        return await extract_account_tier(target, auth_user=auth_user)

    async def reload_current_account_cookies(self, page: CDPPage | None = None) -> bool:
        target = page or (self.active_context.page if self.active_context else None)
        return bool(
            target and await load_cookies_to_page(target, self._auth_file, force_inject=True)
        )

    async def _save_cookies(
        self,
        *,
        auth_file: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> None:
        target = self.page
        if target is not None:
            await save_browser_cookies(
                target, auth_file=auth_file or self._auth_file, cookies=cookies
            )

    async def _ensure_browser_cdp(self) -> CDPPage | None:
        if self._proc is not None and self._proc.is_alive() and self._cdp_client:
            return None

        profile_dir = self._profile_dir
        if profile_dir:
            Path(profile_dir).mkdir(parents=True, exist_ok=True)

        self._proc = launch_chromium_process(
            port=self.port,
            user_data_dir=profile_dir,
            headless=settings.browser_headless,
        )

        self._cdp_client = CDPClient(port=self.port)
        await self._cdp_client.wait_until_ready()
        return await self._cdp_client.connect_page(block_assets=True)

    async def close(self) -> None:
        async with self._transition_lock:
            await self._close_internal()

    async def _close_internal(self) -> None:
        active_ctx = self._gate.active_context
        retiring = list(self._retiring_contexts)
        self._gate.reset()
        self._retiring_contexts.clear()
        self._transport.close("session_closed")

        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        self._background_tasks.clear()

        for ctx in ([active_ctx] if active_ctx is not None else []) + retiring:
            with suppress(Exception):
                if not ctx.page.is_closed():
                    await ctx.page.close()

        if self._cdp_client is not None:
            with suppress(Exception):
                await self._cdp_client.close()
            self._cdp_client = None

        if self._proc is not None:
            with suppress(Exception):
                self._proc.terminate()
            self._proc = None


__all__ = [
    "DEFAULT_BOOTSTRAP_TEMPLATE",
    "BrowserSession",
    "_is_aistudio_page_url",
    "_is_login_page_url",
]
