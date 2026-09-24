"""Shared browser session management for gateway operations.

Uses native async CDP engine for zero-Node, fast and memory-efficient
Chromium automation with kernel-level asset pruning.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.domain.errors import SessionExpiredError
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.browser.browser_engine import (
    ChromiumProcess,
    launch_chromium_process,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    CHECK_IDENTITY_JS,
    DIALOG_CLEANUP_JS,
    DOM_GC_CLEANUP_JS,
    INSTALL_HOOKS_JS,
    SNAPSHOT_GENERATE_JS,
    STOP_GENERATION_JS,
)
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session")

AI_STUDIO_URL = "https://aistudio.google.com/prompts/new_chat?model=gemini-3.7-flash"
AI_STUDIO_URL_FALLBACK = "https://aistudio.google.com/app/prompts/new_chat"
GOOGLE_LOGIN_BOOTSTRAP_URL = (
    "https://accounts.google.com/ServiceLogin?continue=https://aistudio.google.com"
)
BOTGUARD_BOOTSTRAP_PROMPT = "say '1'"
TEMPLATE_CAPTURE_PROMPT = "say 't'"


def _is_login_page_url(url: str | None) -> bool:
    if not url:
        return False
    u = url.lower()
    return (
        "accounts.google.com" in u
        or "signin" in u
        or "servicelogin" in u
        or "accountchooser" in u
    )


DEFAULT_BOOTSTRAP_TEMPLATE = {
    "url": "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent",
    "headers": {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "X-Goog-Api-Key": "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs",
        "X-Goog-AuthUser": "0",
        "Referer": "https://aistudio.google.com/",
    },
    "body": '["models/gemini-3.7-flash",[[[[null,"hi"]],"user"]],[[null,null,7,5],[null,null,8,5],[null,null,9,5],[null,null,10,5]],[null,null,null,65536,1,0.95,64,null,null,null,null,null,null,1,null,null,[1,null,null,2]],"!snap",null,null,null,null,null,1,null]',
}


class BrowserSession:
    """Manages browser lifecycle, CDP connection, page hooks, and request replay."""

    def __init__(self, port: int):
        self.port = port
        self._auth_file = settings.auth_file or self._discover_active_auth_file()
        self._profile_dir = self._derive_profile_dir(self._auth_file)
        self._proc: ChromiumProcess | None = None
        self._cdp_client: CDPClient | None = None
        self._page: CDPPage | None = None
        self._snap_key: str | None = None
        self._bootstrap_template: dict[str, object] | None = None
        self._transport = XHRStreamTransport()
        self._lock = asyncio.Lock()
        self._botguard_lock = asyncio.Lock()
        self._template_lock = asyncio.Lock()
        self._in_flight: int = 0
        self._switching: bool = False
        self._switch_event = asyncio.Event()
        self._switch_event.set()
        self._last_activity_time: float = time.time()
        self._hooks_installed: bool = False
        self._stream_cleanup_count: int = 0

    def get_current_auth_user(self) -> str:
        """获取当前活跃账号的 auth_user 编号（0, 1, 2...）。"""
        if self._auth_file:
            try:
                meta_path = Path(self._auth_file).parent / "meta.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    return str(meta.get("auth_user") or "0")
            except Exception:
                pass
        try:
            store = AccountStore()
            acc = store.get_active_account()
            if acc and acc.auth_user:
                return str(acc.auth_user)
        except Exception:
            pass
        return "0"

    @asynccontextmanager
    async def request_scope(self):
        """追踪正在进行的请求，防止切号时进程被强杀造成断流，并更新活跃时间戳。"""
        await self._switch_event.wait()
        self._in_flight += 1
        self._last_activity_time = time.time()
        try:
            yield
        finally:
            self._in_flight = max(0, self._in_flight - 1)
            self._last_activity_time = time.time()

    async def check_idle_timeout(self) -> bool:
        """检查浏览器是否空闲超时，超时则主动释放进程以回收 300-500MB 内存。"""
        idle_timeout = getattr(settings, "browser_idle_timeout", 0)
        if idle_timeout <= 0:
            return False
        async with self._lock:
            if self._in_flight <= 0 and self._proc is not None:
                idle_duration = time.time() - self._last_activity_time
                if idle_duration >= idle_timeout:
                    log.info(
                        "浏览器已空闲 %.1f 秒 (阈值=%d 秒)，主动回收进程以释放内存",
                        idle_duration,
                        idle_timeout,
                    )
                    await self._close_internal()
                    return True
        return False

    async def reload_current_account_cookies(self, page: CDPPage | None = None) -> bool:
        """重新清理并注入当前活跃账号的原始 auth.json Cookie。"""
        if not self._auth_file or not Path(self._auth_file).is_file():
            return False
        target_page = page or self._page
        if target_page is None or target_page.is_closed():
            return False
        try:
            log.info("检测到登录页重定向，正在重新载入 Cookie: %s", self._auth_file)
            with suppress(Exception):
                await target_page.cdp.send("Network.clearBrowserCookies")
                await target_page.cdp.send("Network.clearBrowserCache")
            data = json.loads(Path(self._auth_file).read_text(encoding="utf-8"))
            cookies = data.get("cookies") or []
            if cookies:
                # 恢复登录重定向时剔除易冲突的旧 OSID / SIDCC，由 Google SSO 自然重新协商签发
                cleaned_cookies = [
                    c
                    for c in cookies
                    if str(c.get("name") or "")
                    not in {
                        "OSID",
                        "__Secure-OSID",
                        "SIDCC",
                        "__Secure-1PSIDCC",
                        "__Secure-3PSIDCC",
                    }
                ]
                await target_page.set_cookies(cleaned_cookies or cookies)
            await self._install_hooks(target_page)
            if not _is_login_page_url(target_page.url):
                log.info("Cookie 重新载入成功，会话已恢复: %s", self._auth_file)
                return True
        except Exception as e:
            log.warning("重新载入 Cookie 失败: %s", e)
        return False

    async def is_alive(self, timeout_s: float = 1.5) -> bool:
        """Fast non-blocking probe to verify if the browser process and CDP page are responsive."""
        if self._proc is not None and not self._proc.is_alive():
            return False
        if self._page is None or self._page.is_closed():
            return False
        return await self._page.is_alive(timeout_s=timeout_s)

    async def ensure_context(self) -> CDPPage:
        """Ensure Chromium process is running and CDPPage is connected and responsive."""
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                if self._hooks_installed:
                    return self._page
                if (
                    self._proc is None or self._proc.is_alive()
                ) and await self._page.is_alive(timeout_s=1.5):
                    return self._page
                log.warning("浏览器无响应或进程已退出，正在重新连接...")

            await self._close_internal()
            return await self._ensure_browser_cdp()

    async def switch_auth(self, auth_file: str | None) -> None:
        """Switch active auth file and reload cookies into current page without killing Chromium."""
        async with self._lock:
            self._switch_event.clear()
            self._switching = True
            try:
                # 等待正在处理的请求排干，最多等待 15 秒，避免直接切号杀进程导致进行中的流断连
                for _ in range(150):
                    if self._in_flight <= 0:
                        break
                    await asyncio.sleep(0.1)

                self._auth_file = auth_file
                self._profile_dir = self._derive_profile_dir(auth_file)
                self._bootstrap_template = None
                self._snap_key = None
                self._hooks_installed = False
                hot_switched = False
                if self._page is not None and not self._page.is_closed():
                    try:
                        # 0. 清理页面中上一个账号的 BotGuardService 和快照状态
                        with suppress(Exception):
                            await self._page.evaluate(
                                "() => { window.__bg_service = null; window.__bg_snapshot = null; window.__bg_hooked = false; window.__snap_key = null; }"
                            )

                        # 1. 清理当前会话 Cookie 与 Cache
                        with suppress(Exception):
                            await self._page.cdp.send("Network.clearBrowserCookies")
                            await self._page.cdp.send("Network.clearBrowserCache")
                        # 2. 读取目标账号的 Cookie 并注入当前页面上下文
                        if auth_file and Path(auth_file).exists():
                            data = json.loads(
                                Path(auth_file).read_text(encoding="utf-8")
                            )
                            new_cookies = data.get("cookies") or []
                            if new_cookies:
                                cleaned_cookies = [
                                    c
                                    for c in new_cookies
                                    if str(c.get("name") or "")
                                    not in {
                                        "OSID",
                                        "__Secure-OSID",
                                        "SIDCC",
                                        "__Secure-1PSIDCC",
                                        "__Secure-3PSIDCC",
                                    }
                                ]
                                await self._page.set_cookies(cleaned_cookies or new_cookies)
                        # 3. 页面导航至目标账号对应的 /u/{auth_user}/ 路径并执行 DOM GC 清理
                        await self._goto_aistudio(self._page)
                        await self._install_hooks(self._page)
                        with suppress(Exception):
                            await self._page.evaluate(DOM_GC_CLEANUP_JS)
                        hot_switched = True
                        log.info(
                            "账号热切成功，复用单进程 (auth_file=%s)",
                            auth_file,
                        )
                    except Exception as e:
                        log.warning("账号热切失败，降级重启浏览器: %s", e)

                if not hot_switched:
                    await self._close_internal()
            finally:
                self._switching = False
                self._switch_event.set()

    async def ensure_hook_page(self) -> bool:
        """Ensure page is navigated to AI Studio and hooks are installed."""
        page = await self.ensure_context()
        if "aistudio.google.com" not in (page.url or ""):
            await self._goto_aistudio(page)
        await self._install_hooks(page)
        return True

    async def ensure_botguard_service(self, force_refresh: bool = False) -> CDPPage:
        """Ensure BotGuardService is captured in page context."""
        if (
            not force_refresh
            and self._page is not None
            and not self._page.is_closed()
            and self._hooks_installed
            and self._snap_key
            and self._bootstrap_template is not None
        ):
            return self._page

        page = await self.ensure_context()
        if "aistudio.google.com" not in (page.url or ""):
            await self._goto_aistudio(page)
        await self._install_hooks(page)

        if not force_refresh and (
            (self._hooks_installed and self._bootstrap_template is not None and self._snap_key)
            or (await page.evaluate("() => !!window.__bg_service") and self._bootstrap_template is not None and self._snap_key)
        ):
            return page

        async with self._botguard_lock:
            if not force_refresh and (
                (self._hooks_installed and self._bootstrap_template is not None and self._snap_key)
                or (await page.evaluate("() => !!window.__bg_service") and self._bootstrap_template is not None and self._snap_key)
            ):
                return page

            if force_refresh:
                self._hooks_installed = False
                with suppress(Exception):
                    await page.evaluate(
                        "() => { window.__bg_service = null; window.__bg_snapshot = null; window.__bg_hooked = false; window.__snap_key = null; }"
                    )
                self._bootstrap_template = None
                await self._goto_aistudio(page)
                await self._install_hooks(page)
            current_url = page.url or ""
            if "available-regions" in current_url:
                raise RuntimeError(
                    "Google AI Studio 地区限制: 访问被重定向至 available-regions"
                )

            t0 = time.time()
            captured: dict[str, object] = {}
            original_text: str = ""

            def on_req(req: dict[str, object]) -> None:
                url = str(req.get("url") or "")
                if "GenerateContent" not in url or "Count" in url or captured:
                    return
                body = str(req.get("post_data") or "")
                if not body:
                    return
                captured["url"] = url
                captured["headers"] = req.get("headers") or {}
                captured["body"] = body

            unsub = page.on_request(on_req)
            await page.evaluate(DIALOG_CLEANUP_JS)
            await page.evaluate(
                """(() => {
                    const clickable = Array.from(document.querySelectorAll('button, mat-card, a, [role="button"]'));
                    const target = clickable.find(el => (el.innerText || el.textContent || '').includes('Code and Chat'));
                    if (target) { target.click(); return; }
                    const startBtn = clickable.find(el => (el.innerText || el.textContent || '').includes('Start building'));
                    if (startBtn) { startBtn.click(); return; }
                })()"""
            )
            await page.wait_for_timeout(1000)
            try:
                original_text = ""
                try:
                    await page.wait_for_selector("textarea", timeout_s=15.0)
                except Exception as err:
                    dbg_url = page.url or ""
                    if _is_login_page_url(dbg_url):
                        log.warning(
                            "等待输入框期间检测到登录页重定向: %s，尝试重新注入 Cookie...",
                            dbg_url,
                        )
                        if await self.reload_current_account_cookies(page):
                            await page.wait_for_selector("textarea", timeout_s=15.0)
                        else:
                            raise SessionExpiredError(
                                f"Cookie 认证失效，已被重定向到 Google 登录页: {dbg_url}"
                            ) from err
                    if "available-regions" in (dbg_url or ""):
                        raise RuntimeError(
                            "Google AI Studio 地区限制: 访问被重定向至 available-regions"
                        ) from err
                    dbg_title = await page.title()
                    raw_dbg_body = await page.evaluate(
                        "() => document.body?.innerText?.substring(0, 300) || ''"
                    )
                    dbg_body = str(raw_dbg_body or "")
                    raise RuntimeError(
                        f"textarea not found while capturing BotGuardService; url={dbg_url}, title={dbg_title}, body={dbg_body[:200]}"
                    ) from err
                original_text = str(
                    (
                        await page.evaluate(
                            "() => document.querySelector('textarea')?.value || ''"
                        )
                    )
                    or ""
                )
                await page.fill("textarea", BOTGUARD_BOOTSTRAP_PROMPT)
                await page.wait_for_timeout(800)
                await page.evaluate(DIALOG_CLEANUP_JS)

                if not await self._click_run_button(page):
                    raise RuntimeError(
                        "failed to trigger send while capturing BotGuardService"
                    )

                for i in range(45):
                    if self._proc is not None and not self._proc.is_alive():
                        raise RuntimeError(
                            "Browser process died during BotGuard capture"
                        )
                    if page.is_closed():
                        raise RuntimeError(
                            "Browser page closed during BotGuard capture"
                        )
                    curr_url = page.url or ""
                    if _is_login_page_url(curr_url):
                        log.warning(
                            "BotGuard 捕获期间检测到登录页重定向: %s，尝试重新注入 Cookie...",
                            curr_url,
                        )
                        if await self.reload_current_account_cookies(page):
                            break
                        raise SessionExpiredError(
                            f"Cookie 认证失效，已跳转至登录页: {curr_url}"
                        )
                    await page.wait_for_timeout(1000)
                    if await page.evaluate("() => !!window.__bg_service"):
                        # 预热即刻截断：捕获到 BotGuardService 后立即停止生成，无需等待模型吐字
                        with suppress(Exception):
                            await page.evaluate(STOP_GENERATION_JS)
                        if not captured:
                            for _ in range(20):
                                if captured:
                                    break
                                await page.wait_for_timeout(100)
                        if captured and self._bootstrap_template is None:
                            self._bootstrap_template = dict(captured)
                        await page.fill("textarea", original_text)
                        # 执行 DOM 垃圾回收，消除历史对话 DOM 堆积
                        with suppress(Exception):
                            await page.evaluate(DOM_GC_CLEANUP_JS)
                        log.debug(
                            "BotGuard 捕获成功，耗时 %d 秒 (累计 %.1f 秒)",
                            i + 1,
                            time.time() - t0,
                        )
                        return page
                raise RuntimeError("BotGuardService capture timeout")
            finally:
                unsub()
                with suppress(Exception):
                    await page.fill("textarea", original_text)
    async def import_cookies(
        self, cookie_string: str, auth_file: str | None = None
    ) -> int:
        """Inject cookie string, navigate through Google surfaces, and save cookies."""
        from aistudio_api.infrastructure.account.cookie_refresher import (
            load_cookies_from_string,
        )

        pw_cookies = load_cookies_from_string(cookie_string)
        target_auth_file = auth_file or self._auth_file
        original_auth_file = self._auth_file
        original_profile_dir = self._profile_dir

        if not target_auth_file:
            return len(pw_cookies)

        # Seed target auth file
        await self._save_cookies(auth_file=target_auth_file, cookies=pw_cookies)

        switched_target = False
        if (
            self._proc is None
            or not original_auth_file
            or Path(target_auth_file).resolve() != Path(original_auth_file).resolve()
        ):
            await self.switch_auth(target_auth_file)
            switched_target = True

        page = await self.ensure_context()
        await page.set_cookies(pw_cookies)

        try:
            await self._bootstrap_google_session(page)
        except Exception as e:
            log.warning("导入 Cookie 时访问页面失败: %s", e)

        try:
            browser_cookies = await page.get_cookies()
            if browser_cookies:
                await self._save_cookies(
                    auth_file=target_auth_file, cookies=browser_cookies
                )
                log.info(
                    "已从浏览器导出 %d 个 Cookie",
                    len(browser_cookies),
                )
            else:
                await self._save_cookies(auth_file=target_auth_file, cookies=pw_cookies)
            return len(browser_cookies or pw_cookies)
        finally:
            if switched_target and original_auth_file:
                await self.switch_auth(original_auth_file)
                self._profile_dir = original_profile_dir

    async def capture_template_flow(self, model: str) -> dict[str, object]:
        """Execute browser action flow to capture request template without caching in session."""
        async with self._template_lock:
            page = await self.ensure_botguard_service()
            if not self._bootstrap_template:
                await self.ensure_botguard_service(force_refresh=True)
            if self._bootstrap_template:
                return dict(self._bootstrap_template)
            captured: dict[str, object] = {}
            last_response: dict[str, object] | None = None

            def on_req(req: dict[str, object]) -> None:
                url = str(req.get("url") or "")
                if "GenerateContent" not in url or "Count" in url or captured:
                    return
                body = str(req.get("post_data") or "")
                if not body or len(body) <= 100:
                    return
                captured["url"] = url
                captured["headers"] = req.get("headers") or {}
                captured["body"] = body

            def on_resp(resp: dict[str, object]) -> None:
                nonlocal last_response
                url = str(resp.get("url") or "")
                if "GenerateContent" not in url or "Count" in url:
                    return
                last_response = resp

            unsub_req = page.on_request(on_req)
            unsub_resp = page.on_response(on_resp)
            original_text = ""
            try:
                original_text = str(
                    (
                        await page.evaluate(
                            "() => document.querySelector('textarea')?.value || ''"
                        )
                    )
                    or ""
                )
                await page.fill("textarea", TEMPLATE_CAPTURE_PROMPT)
                await page.wait_for_timeout(500)
                if not await self._click_run_button(page):
                    raise RuntimeError("failed to trigger send during template capture")

                for _ in range(30):
                    if self._proc is not None and not self._proc.is_alive():
                        raise RuntimeError(
                            "Browser process died during template capture"
                        )
                    if page.is_closed():
                        raise RuntimeError(
                            "Browser page closed during template capture"
                        )
                    curr_url = page.url or ""
                    if _is_login_page_url(curr_url):
                        log.warning(
                            "模板捕获期间检测到登录页重定向: %s，尝试重新注入 Cookie...",
                            curr_url,
                        )
                        if await self.reload_current_account_cookies(page):
                            await page.fill("textarea", TEMPLATE_CAPTURE_PROMPT)
                            await page.wait_for_timeout(500)
                            await self._click_run_button(page)
                            continue
                        raise SessionExpiredError(
                            f"Cookie 认证失效，已跳转至登录页: {curr_url}"
                        )
                    await page.wait_for_timeout(1000)
                    if captured:
                        with suppress(Exception):
                            await page.evaluate(STOP_GENERATION_JS)
                        break
                if not captured:
                    if self._bootstrap_template:
                        log.warning(
                            "模型 %s 动态模板捕获超时，回退至引导模板",
                            model,
                        )
                        return dict(self._bootstrap_template)
                    if last_response is not None:
                        raise RuntimeError(
                            f"template capture failed after request: status={last_response.get('status')} url={last_response.get('url')}"
                        )
                    raise RuntimeError(f"template capture timeout for model={model}")

                await page.fill("textarea", original_text)
                with suppress(Exception):
                    await page.evaluate(DOM_GC_CLEANUP_JS)
                return captured
            finally:
                unsub_req()
                unsub_resp()
                with suppress(Exception):
                    await page.fill("textarea", original_text)

    async def capture_template(self, model: str) -> dict[str, object]:
        """Capture template flow forwarder."""
        return await self.capture_template_flow(model)

    async def generate_snapshot(self, contents: list[AistudioContent]) -> str:
        """Generate a BotGuard snapshot token for given content payload.

        Matches Google AI Studio's official _.Nv(request) algorithm:
        For each content in contents, extract each part as a string:
          - text part -> part.text (or "" if None)
          - inline_data part -> part.inline_data[1] (base64 data)
          - file_id / file_data part -> part.file_id (or "" if None)
          - other parts (function_call, function_response, etc.) -> ""
        All extracted parts are joined by a single space (" ") and hashed via SHA-256 hex digest.
        """
        page = await self.ensure_botguard_service()
        if not self._snap_key:
            raise RuntimeError("Snapshot function not detected")

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
        content_hash = sha256(" ".join(hash_parts).encode("utf-8")).hexdigest()
        # 直接使用 Promise 求值，完全隔离每个并发调用的结果，避免污染 window 全局变量
        script = SNAPSHOT_GENERATE_JS
        for attempt in range(3):
            try:
                snapshot = await page.evaluate(
                    script, args=content_hash, timeout_s=10.0
                )
                if snapshot and isinstance(snapshot, str) and len(snapshot) > 0:
                    return snapshot
            except Exception as e:
                log.debug("异步计算快照第 %d 次尝试失败: %s", attempt + 1, e)
                if attempt < 2:
                    await page.wait_for_timeout(300)
                    with suppress(Exception):
                        await self.ensure_botguard_service()

        raise RuntimeError(
            f"Snapshot generation failed for content hash {content_hash[:8]}"
        )

    async def send_hooked_request(
        self,
        *,
        body: str,
        timeout_ms: int,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Replay request via XHR inside the browser context."""
        async with self.request_scope():
            page = await self.ensure_botguard_service()
            if url and headers is not None:
                captured_url = url
                captured_headers = headers
            else:
                template = await self.capture_template_flow(model="gemini-3.7-flash")
                captured_url = str(template.get("url") or "")
                raw_hdrs = template.get("headers")
                captured_headers = {
                    str(k): str(v)
                    for k, v in (raw_hdrs.items() if isinstance(raw_hdrs, dict) else [])
                }

            return await self._transport.send_hooked_request(
                page,
                url=captured_url,
                headers=captured_headers,
                body=body,
                timeout_ms=timeout_ms,
                auth_user=self.get_current_auth_user(),
            )

    async def send_streaming_request(
        self,
        *,
        body: str,
        timeout_ms: int,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Send a streaming request, yielding ('status', int) and ('chunk', bytes) events."""
        async with self.request_scope():
            page = await self.ensure_botguard_service()
            if url and headers is not None:
                captured_url = url
                captured_headers = headers
            else:
                template = await self.capture_template_flow(model="gemini-3.7-flash")
                captured_url = str(template.get("url") or "")
                raw_hdrs = template.get("headers")
                captured_headers = {
                    str(k): str(v)
                    for k, v in (raw_hdrs.items() if isinstance(raw_hdrs, dict) else [])
                }

            try:
                async for event in self._transport.send_streaming_request(
                    page,
                    url=captured_url,
                    headers=captured_headers,
                    body=body,
                    timeout_ms=timeout_ms,
                    auth_user=self.get_current_auth_user(),
                ):
                    yield event
            finally:
                await self.cleanup_stream_page()

    async def cleanup_stream_page(self) -> None:
        """Execute post-stream DOM cleanup and throttled V8 garbage collection."""
        self._stream_cleanup_count += 1
        if self._page is not None and not self._page.is_closed():
            with suppress(Exception):
                await self._page.evaluate(DOM_GC_CLEANUP_JS)
            if self._in_flight <= 0 and (self._stream_cleanup_count % 10 == 0):
                with suppress(Exception):
                    await self._page.collect_garbage()
                import gc

                gc.collect()

    async def close(self) -> None:
        """Close browser session and free resources."""
        async with self._lock:
            await self._close_internal()

    async def _close_internal(self) -> None:
        if self._cdp_client is not None:
            with suppress(Exception):
                await self._cdp_client.close()
            self._cdp_client = None

        if self._proc is not None:
            with suppress(Exception):
                self._proc.terminate()
            self._proc = None

        self._page = None
        self._snap_key = None
        self._bootstrap_template = None
        self._hooks_installed = False

    async def _ensure_browser_cdp(self) -> CDPPage:
        """Launch Chromium subprocess and connect async CDP client."""
        profile_dir = self._profile_dir
        if profile_dir:
            profile_path = Path(profile_dir)
            profile_path.mkdir(parents=True, exist_ok=True)

        self._proc = launch_chromium_process(
            port=self.port,
            user_data_dir=profile_dir,
            headless=settings.browser_headless,
        )

        self._cdp_client = CDPClient(port=self.port)
        self._page = await self._cdp_client.connect_page(block_assets=True)
        self._page.set_liveness_checker(
            lambda: self._proc is None or self._proc.is_alive()
        )
        with suppress(Exception):
            tz_id = settings.timezone
            await self._page.cdp.send(
                "Emulation.setTimezoneOverride", {"timezoneId": tz_id}
            )
            await self._page.cdp.send(
                "Emulation.setLocaleOverride", {"locale": settings.locale}
            )
        if self._auth_file and Path(self._auth_file).exists():
            try:
                data = json.loads(Path(self._auth_file).read_text(encoding="utf-8"))
                cached = data.get("cookies") or []
                if cached:
                    with suppress(Exception):
                        await self._page.cdp.send("Network.clearBrowserCookies")
                        await self._page.cdp.send("Network.clearBrowserCache")
                    cleaned_cached = [
                        c
                        for c in cached
                        if str(c.get("name") or "")
                        not in {
                            "OSID",
                            "__Secure-OSID",
                            "SIDCC",
                            "__Secure-1PSIDCC",
                            "__Secure-3PSIDCC",
                        }
                    ]
                    await self._page.set_cookies(cleaned_cached or cached)
                    log.info("已从 %s 载入 %d 个 Cookie", self._auth_file, len(cleaned_cached or cached))
            except Exception as e:
                log.debug("从 %s 载入 Cookie 失败: %s", self._auth_file, e)
        await self._goto_aistudio(self._page)
        await self._install_hooks(self._page)
        return self._page

    async def _prepare_streaming(self) -> tuple[CDPPage, str, dict[str, str]]:
        page = await self.ensure_botguard_service()
        from aistudio_api.config import DEFAULT_TEXT_MODEL

        tpl = await self.capture_template_flow(DEFAULT_TEXT_MODEL)
        raw_url = str(tpl.get("url") or "")
        raw_headers = tpl.get("headers")
        headers_dict = raw_headers if isinstance(raw_headers, dict) else {}
        headers = {
            str(k): str(v)
            for k, v in headers_dict.items()
            if str(k).lower() not in ("host", "content-length")
        }
        return page, raw_url, headers

    async def _bootstrap_google_session(self, page: CDPPage) -> None:
        await page.goto(
            GOOGLE_LOGIN_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout_s=30.0
        )
        await page.wait_for_timeout(3000)
        for url in (AI_STUDIO_URL, AI_STUDIO_URL_FALLBACK):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout_s=30.0)
                await page.wait_for_timeout(1500)
                if "accounts.google.com" not in (page.url or ""):
                    return
            except Exception:
                continue
        raise RuntimeError(f"bootstrap stayed on login flow: url={page.url}")

    def _get_aistudio_url(self, model: str = "gemini-3.7-flash") -> list[str]:
        """根据当前活跃账号的 auth_user 生成访问 URL。"""
        auth_user = self.get_current_auth_user()
        if auth_user and auth_user != "0":
            return [
                f"https://aistudio.google.com/u/{auth_user}/prompts/new_chat?model={model}",
                f"https://aistudio.google.com/u/{auth_user}/app/prompts/new_chat",
            ]
        return [AI_STUDIO_URL, AI_STUDIO_URL_FALLBACK]

    async def _goto_aistudio(self, page: CDPPage) -> None:
        last_exc = None
        target_urls = self._get_aistudio_url()
        for url in target_urls:
            try:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout_s=20.0)
                except RuntimeError as nav_exc:
                    if "net::ERR_ABORTED" in str(nav_exc):
                        curr = page.url or ""
                        try:
                            eval_url = await page.evaluate(
                                "() => window.location.href", timeout_s=2.0
                            )
                            if eval_url:
                                curr = str(eval_url)
                        except Exception:
                            pass
                        auth_user = self.get_current_auth_user()
                        expected_match = (
                            f"/u/{auth_user}/"
                            if auth_user and auth_user != "0"
                            else "aistudio.google.com"
                        )
                        if "aistudio.google.com" in curr and (
                            expected_match in curr
                            or expected_match == "aistudio.google.com"
                        ):
                            log.debug(
                                "页面导航被终止但已处于 AI Studio 域: %s",
                                curr,
                            )
                        else:
                            raise
                    else:
                        raise

                current_url = page.url or ""
                try:
                    eval_url = await page.evaluate(
                        "() => window.location.href", timeout_s=2.0
                    )
                    if eval_url:
                        current_url = str(eval_url)
                except Exception:
                    pass

                if "available-regions" in current_url:
                    raise RuntimeError(
                        f"Google AI Studio 地区限制 (IP 漏了/不支持): {current_url}"
                    )
                if _is_login_page_url(current_url):
                    log.warning(
                        "访问 AI Studio 后检测到重定向至登录页: %s，尝试重新注入 Cookie...",
                        current_url,
                    )
                    if await self.reload_current_account_cookies(page):
                        return
                    raise SessionExpiredError(
                        f"Cookie 认证失败，已被重定向到 Google 登录页。 (url={current_url})"
                    )
                with suppress(Exception):
                    await page.evaluate(DIALOG_CLEANUP_JS)

                for _ in range(15):
                    with suppress(Exception):
                        if await page.evaluate("() => !!window.default_MakerSuite"):
                            break
                    await page.wait_for_timeout(500)

                if await self._verify_account_identity(page):
                    await self._save_cookies()
                return
            except Exception as exc:
                if "地区限制" in str(exc) or "Cookie 认证失败" in str(exc):
                    raise exc
                log.debug("访问 %s 失败: %s", url, exc)
                last_exc = exc
        if last_exc is not None:
            raise last_exc

    async def _install_hooks(self, page: CDPPage) -> None:
        result = await page.evaluate(INSTALL_HOOKS_JS)
        if result == "already_hooked":
            self._hooks_installed = True
            return
        if isinstance(result, str) and result.startswith("hooked:"):
            self._snap_key = result.split(":", 1)[1]
            self._hooks_installed = True
            return
        for _ in range(3):
            await page.wait_for_timeout(2000)
            result = await page.evaluate(INSTALL_HOOKS_JS)
            if result == "already_hooked":
                self._hooks_installed = True
                return
            if isinstance(result, str) and result.startswith("hooked:"):
                self._snap_key = result.split(":", 1)[1]
                self._hooks_installed = True
                return
        page_url = page.url if page else "(no page)"
        page_title = await page.title() if page else ""
        raise RuntimeError(
            f"Hook install failed: {result} (url={page_url}, title={page_title!r})"
        )

    async def _click_run_button(self, page: CDPPage) -> bool:
        clicked = await page.evaluate(
            """(() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const runBtn = buttons.find(b => {
                    const t = (b.innerText || b.textContent || '').trim();
                    return t === 'Run' || t.startsWith('Run\\n') || t.startsWith('Run Ctrl')
                        || t === 'Build' || t.startsWith('Build\\n') || t.startsWith('BuildCtrl')
                        || b.classList.contains('build-button')
                        || b.classList.contains('ctrl-enter-submits');
                });
                if (runBtn) {
                    runBtn.click();
                    return true;
                }
                return false;
            })()"""
        )
        if clicked:
            return True

        if await page.send_control_enter("textarea"):
            return True

        if await page.click("button.build-button"):
            return True
        if await page.click("button.ctrl-enter-submits"):
            return True
        if await page.click("button:has-text('Build')"):
            return True
        if await page.click("button:has-text('Run')"):
            return True
        return await page.click("button:has(mat-icon)")

    async def _has_run_button(self, page: CDPPage) -> bool:
        try:
            return bool(
                await page.evaluate(
                    """(() => {
                        const buttons = Array.from(document.querySelectorAll('button'));
                        const hasStop = buttons.some(b => {
                            const t = (b.innerText || b.textContent || '').trim();
                            return t === 'Stop' || t.startsWith('Stop') || b.classList.contains('stop-button');
                        });
                        if (hasStop) return false;
                        return buttons.some(b => {
                            const t = (b.innerText || b.textContent || '').trim();
                            return t === 'Run' || t.startsWith('Run')
                                || t === 'Build' || t.startsWith('Build')
                                || b.classList.contains('build-button')
                                || b.classList.contains('ctrl-enter-submits');
                        });
                    })()"""
                )
            )
        except Exception:
            return False

    async def _wait_until_idle(self, page: CDPPage) -> None:
        for _ in range(25):
            if self._proc is not None and not self._proc.is_alive():
                raise RuntimeError("Browser process died while waiting for idle")
            if page.is_closed():
                raise RuntimeError("Page closed while waiting for idle")
            if await self._has_run_button(page):
                return
            await page.wait_for_timeout(1000)
        is_running = await page.evaluate(
            """(() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                return buttons.some(b => (b.innerText || b.textContent || '').trim().startsWith('Stop') || b.classList.contains('stop-button'));
            })()"""
        )
        if not is_running:
            return
        raise RuntimeError("page never became idle")

    async def _verify_account_identity(self, page: CDPPage) -> bool:
        auth_file = self._auth_file
        if not auth_file:
            return True
        meta_path = Path(auth_file).parent / "meta.json"
        if not meta_path.exists():
            return True
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return True
        expected_email = meta.get("email") or ""
        if not expected_email:
            return True

        is_verified = False
        with suppress(Exception):
            is_verified = bool(await page.evaluate(CHECK_IDENTITY_JS, expected_email))

        if not is_verified:
            with suppress(Exception):
                cookies = await page.get_cookies()
                for c in cookies:
                    if expected_email in str(c.get("value", "")):
                        is_verified = True
                        break
        if is_verified:
            return True

        account_id = meta.get("id", "unknown")
        log.warning(
            "[account-guard] 页面未校验到期望账号 %s (%s)，跳过本次 Cookie 回写以防覆盖",
            expected_email,
            account_id,
        )
        return False

    async def _save_cookies(
        self,
        *,
        auth_file: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> None:
        target_auth_file = auth_file or self._auth_file
        if not target_auth_file:
            return
        try:
            current_cookies = cookies
            if current_cookies is None:
                if self._page is None:
                    return
                current_cookies = await self._page.get_cookies()
            if not current_cookies:
                return
            auth_path = Path(target_auth_file)
            origins = []
            if auth_path.exists():
                try:
                    existing = json.loads(auth_path.read_text(encoding="utf-8"))
                    origins = existing.get("origins", [])
                except Exception:
                    pass
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"cookies": current_cookies, "origins": origins}, indent=2)
            )
            log.info(f"Saved {len(current_cookies)} cookies to {target_auth_file}")
        except Exception as e:
            log.debug(f"Failed to save cookies: {e}")

    @staticmethod
    def _discover_active_auth_file() -> str | None:
        try:
            store = AccountStore()
            account = store.get_active_account()
            if account is None:
                return None
            path = store.get_auth_path_optional(account.id, require_exists=False)
            return str(path) if path is not None else None
        except Exception:
            return None

    @staticmethod
    def _derive_profile_dir(auth_file: str | None) -> str | None:
        if not auth_file:
            fallback_auth_file = BrowserSession._discover_active_auth_file()
            if not fallback_auth_file:
                return None
            auth_file = fallback_auth_file
        return str(Path(auth_file).resolve().parent / "profile")
