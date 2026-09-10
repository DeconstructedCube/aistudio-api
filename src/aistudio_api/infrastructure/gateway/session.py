"""Shared browser session management for gateway operations.

Uses native async CDP engine for zero-Node, fast and memory-efficient
Chromium automation with kernel-level asset pruning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Any, AsyncGenerator

from aistudio_api.config import settings
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.browser.browser_engine import (
    ChromiumProcess,
    launch_chromium_process,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent

log = logging.getLogger("aistudio.session")

AI_STUDIO_URL = "https://aistudio.google.com/prompts/new_chat?model=gemini-3.7-flash"
AI_STUDIO_URL_FALLBACK = "https://aistudio.google.com/app/prompts/new_chat"
GOOGLE_LOGIN_BOOTSTRAP_URL = (
    "https://accounts.google.com/ServiceLogin?continue=https://aistudio.google.com"
)

INSTALL_HOOKS_JS = r"""
((() => {
    // Verify hooks are actually present on XHR prototype, not just a stale flag
    const xhrHookAlive = XMLHttpRequest.prototype.open.__api_hooked === true;
    const fetchHookAlive = window.fetch.__api_hooked === true;
    if (window.__bg_hooked && xhrHookAlive && fetchHookAlive) return 'already_hooked';
    // Reset stale flag if hooks are missing
    if (window.__bg_hooked && (!xhrHookAlive || !fetchHookAlive)) window.__bg_hooked = false;

    const dms = window.default_MakerSuite;
    if (!dms) return 'no_default_MakerSuite';

    // Auto-detect snapshot function via feature matching
    let snapKey = null;
    for (const k of Object.keys(dms)) {
        try {
            if (typeof dms[k] !== 'function') continue;
            const src = dms[k].toString();
            if (src.includes('.snapshot({') && src.includes('content') && src.includes('yield')) {
                snapKey = k;
                break;
            }
        } catch(e) {}
    }
    if (!snapKey) return 'no_snapshot_fn';

    // Hook snapshot function to capture service (only if not already hooked)
    if (!dms[snapKey].__api_hooked) {
        const origSnap = dms[snapKey];
        dms[snapKey] = function(...args) {
            window.__bg_service = args[0];
            const result = origSnap.apply(this, args);
            if (result instanceof Promise) return result.then(s => { window.__bg_snapshot = s; return s; });
            window.__bg_snapshot = result;
            return result;
        };
        dms[snapKey].__api_hooked = true;
    }

    // XHR hook for body replacement (always re-install if missing)
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    const hookedOpen = function(method, url, ...args) {
        this.__url = url;
        this.__is_gen = url.includes('GenerateContent') && !url.includes('CountTokens');
        window.__last_hook_url = url;
        return origOpen.call(this, method, url, ...args);
    };
    hookedOpen.__api_hooked = true;
    XMLHttpRequest.prototype.open = hookedOpen;
    XMLHttpRequest.prototype.send = function(body) {
        if (this.__is_gen && window.__pending_body) {
            const captured = window.__pending_body;
            window.__pending_body = null;
            window.__hooked = true;
            window.__last_hook_url = this.__url || '';
            return origSend.call(this, captured);
        }
        return origSend.call(this, body);
    };

    // fetch hook for body replacement (streaming uses fetch)
    const origFetch = window.fetch;
    const hookedFetch = function(input, init) {
        let url = typeof input === 'string' ? input : (input instanceof Request ? input.url : String(input));
        if (url.includes('GenerateContent') && !url.includes('CountTokens') && window.__pending_body) {
            const captured = window.__pending_body;
            window.__pending_body = null;
            window.__hooked = true;
            window.__last_hook_url = url;
            if (init) {
                init.body = captured;
            } else {
                init = { body: captured };
            }
            return origFetch.call(this, input, init);
        }
        return origFetch.call(this, input, init);
    };
    hookedFetch.__api_hooked = true;
    window.fetch = hookedFetch;

    window.__bg_hooked = true;
    window.__snap_key = snapKey;
    return 'hooked:' + snapKey;
})())
"""

DIALOG_CLEANUP_JS = """(() => {
    document.querySelectorAll('button').forEach((button) => {
        const text = (button.textContent || '').trim().toLowerCase();
        if (['dismiss', 'close', 'accept', 'ok', 'agree', 'got it'].includes(text)) {
            button.click();
        }
    });
    document.querySelectorAll('.cdk-overlay-backdrop').forEach((node) => node.remove());
    document.querySelectorAll('.cdk-overlay-container').forEach((node) => node.remove());
})()"""

STREAMING_INIT_JS = """(args) => {
    const rid = args.rid;
    if (!window.__streams) window.__streams = {};

    const existing = window.__streams[rid];
    if (existing && existing.xhr && existing.xhr.readyState !== 4) {
        try { existing.xhr.abort(); } catch (e) {}
    }

    const state = {
        xhr: null,
        events: [],
        waiter: null,
        recvPos: 0,
        statusSent: false,
    };
    window.__streams[rid] = state;

    function push(event) {
        if (state.waiter) {
            const waiter = state.waiter;
            state.waiter = null;
            waiter(event);
            return;
        }
        state.events.push(event);
    }

    function pushStatus(xhr) {
        if (state.statusSent || xhr.readyState < 2) return;
        state.statusSent = true;
        push({type: 'status', status: xhr.status || 0});
    }

    function pushChunk(xhr) {
        if (xhr.readyState < 3) return;
        const chunk = xhr.responseText.substring(state.recvPos);
        if (!chunk) return;
        state.recvPos = xhr.responseText.length;
        push({type: 'chunk', text: chunk});
    }

    if (!window.__stream_next) window.__stream_next = {};
    window.__stream_next[rid] = function(timeoutMs) {
        if (state.events.length) return Promise.resolve(state.events.shift());
        return new Promise((resolve) => {
            let done = false;
            const timer = setTimeout(() => {
                if (done) return;
                done = true;
                if (state.waiter === finish) state.waiter = null;
                resolve({type: 'idle'});
            }, timeoutMs);
            const finish = (event) => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                resolve(event);
            };
            state.waiter = finish;
        });
    };

    if (!window.__stream_abort) window.__stream_abort = {};
    window.__stream_abort[rid] = function() {
        if (state.xhr && state.xhr.readyState !== 4) {
            try { state.xhr.abort(); } catch (e) {}
        }
    };

    var xhr = new XMLHttpRequest();
    xhr.open('POST', args.url);
    var h = args.headers;
    for (var k in h) {
        xhr.setRequestHeader(k, h[k]);
    }
    xhr.withCredentials = true;
    xhr.timeout = args.timeout * 1000;

    xhr.onreadystatechange = function() {
        pushStatus(xhr);
        pushChunk(xhr);
    };
    xhr.onprogress = function() {
        pushStatus(xhr);
        pushChunk(xhr);
    };
    xhr.onload = function() {
        pushStatus(xhr);
        pushChunk(xhr);
        push({type: 'done'});
    };
    xhr.onerror = function() {
        push({type: 'error', message: 'network error'});
    };
    xhr.ontimeout = function() {
        push({type: 'error', message: 'timeout'});
    };
    xhr.onabort = function() {
        push({type: 'aborted'});
    };

    state.xhr = xhr;
    xhr.send(args.body);
}"""

BOTGUARD_BOOTSTRAP_PROMPT = "say '1'"
TEMPLATE_CAPTURE_PROMPT = "say 't'"


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
        self._templates: dict[str, dict[str, Any]] = {}
        self._bootstrap_template: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def ensure_context(self) -> CDPPage:
        """Ensure Chromium process is running and CDPPage is connected."""
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page

            await self._close_internal()
            return await self._ensure_browser_cdp()

    async def switch_auth(self, auth_file: str | None) -> None:
        """Switch active auth file and invalidate browser profile/templates."""
        async with self._lock:
            self._auth_file = auth_file
            self._profile_dir = self._derive_profile_dir(auth_file)
            self._templates.clear()
            self._bootstrap_template = None
            await self._close_internal()

    async def ensure_hook_page(self) -> bool:
        """Ensure page is navigated to AI Studio and hooks are installed."""
        page = await self.ensure_context()
        if "aistudio.google.com" not in (page.url or ""):
            await self._goto_aistudio(page)
        await self._install_hooks(page)
        return True

    async def ensure_botguard_service(self) -> CDPPage:
        """Ensure BotGuardService is captured in page context."""
        page = await self.ensure_context()
        if "aistudio.google.com" not in (page.url or ""):
            await self._goto_aistudio(page)
        await self._install_hooks(page)

        if await page.evaluate("() => !!window.__bg_service"):
            return page

        t0 = time.time()
        captured: dict[str, Any] = {}

        def on_req(req: dict[str, Any]) -> None:
            url = req.get("url", "")
            if "GenerateContent" not in url or "Count" in url or captured:
                return
            body = req.get("post_data", "")
            if not body:
                return
            captured["url"] = url
            captured["headers"] = req.get("headers", {})
            captured["body"] = body

        unsub = page.on_request(on_req)
        await page.evaluate(DIALOG_CLEANUP_JS)

        try:
            # Check textarea presence
            has_textarea = await page.query_selector("textarea")
            if not has_textarea:
                dbg_url = page.url
                dbg_title = await page.title()
                dbg_body = (await page.evaluate("() => document.body?.innerText?.substring(0, 300) || ''")) or ""
                raise RuntimeError(
                    f"textarea not found while capturing BotGuardService; url={dbg_url}, title={dbg_title}, body={dbg_body[:200]}"
                )

            original_text = (await page.evaluate("() => document.querySelector('textarea')?.value || ''")) or ""
            await page.fill("textarea", BOTGUARD_BOOTSTRAP_PROMPT)
            await page.wait_for_timeout(800)
            await page.evaluate(DIALOG_CLEANUP_JS)

            if not await self._click_run_button(page):
                raise RuntimeError("failed to trigger send while capturing BotGuardService")

            for i in range(45):
                await page.wait_for_timeout(1000)
                if await page.evaluate("() => !!window.__bg_service"):
                    await self._wait_until_idle(page)
                    if captured and self._bootstrap_template is None:
                        self._bootstrap_template = dict(captured)
                    await page.fill("textarea", original_text)
                    log.debug(f"[timing] botguard captured after {i+1}s, total {time.time()-t0:.1f}s")
                    return page

            raise RuntimeError("BotGuardService capture timeout")
        finally:
            unsub()
            try:
                await page.fill("textarea", original_text if "original_text" in locals() else "")
            except Exception:
                pass

    async def import_cookies(self, cookie_string: str, auth_file: str | None = None) -> int:
        """Inject cookie string, navigate through Google surfaces, and save cookies."""
        from aistudio_api.infrastructure.account.cookie_refresher import load_cookies_from_string

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
            log.warning("[import_cookies] browser visit failed: %s", e)

        try:
            browser_cookies = await page.get_cookies()
            if browser_cookies:
                await self._save_cookies(auth_file=target_auth_file, cookies=browser_cookies)
                log.info("[import_cookies] exported %d cookies from browser", len(browser_cookies))
            else:
                await self._save_cookies(auth_file=target_auth_file, cookies=pw_cookies)
            return len(browser_cookies or pw_cookies)
        finally:
            if switched_target and original_auth_file:
                await self.switch_auth(original_auth_file)
                self._profile_dir = original_profile_dir

    async def capture_template(self, model: str) -> dict[str, Any]:
        """Capture GenerateContent request headers and URL template for the given model."""
        if model in self._templates:
            return self._templates[model]

        page = await self.ensure_botguard_service()
        if self._bootstrap_template:
            captured = dict(self._bootstrap_template)
            self._templates[model] = captured
            return captured

        captured: dict[str, Any] = {}
        last_response: dict[str, Any] | None = None

        def on_req(req: dict[str, Any]) -> None:
            url = req.get("url", "")
            if "GenerateContent" not in url or "Count" in url or captured:
                return
            body = req.get("post_data", "")
            if not body or len(body) <= 100:
                return
            captured["url"] = url
            captured["headers"] = req.get("headers", {})
            captured["body"] = body

        def on_resp(resp: dict[str, Any]) -> None:
            nonlocal last_response
            url = resp.get("url", "")
            if "GenerateContent" not in url or "Count" in url:
                return
            last_response = resp

        unsub_req = page.on_request(on_req)
        unsub_resp = page.on_response(on_resp)
        try:
            original_text = (await page.evaluate("() => document.querySelector('textarea')?.value || ''")) or ""
            await page.fill("textarea", TEMPLATE_CAPTURE_PROMPT)
            await page.wait_for_timeout(500)
            if not await self._click_run_button(page):
                raise RuntimeError("failed to trigger send during template capture")

            for _ in range(30):
                await page.wait_for_timeout(1000)
                if captured:
                    break

            if not captured:
                if last_response is not None:
                    raise RuntimeError(
                        f"template capture failed after request: status={last_response.get('status')} url={last_response.get('url')}"
                    )
                raise RuntimeError(f"template capture timeout for model={model}")

            await self._wait_until_idle(page)
            await page.fill("textarea", original_text)
            self._templates[model] = captured
            return captured
        finally:
            unsub_req()
            unsub_resp()
            try:
                await page.fill("textarea", original_text if "original_text" in locals() else "")
            except Exception:
                pass

    async def generate_snapshot(self, contents: list[AistudioContent]) -> str:
        """Generate a BotGuard snapshot token for given content payload."""
        page = await self.ensure_botguard_service()
        if not self._snap_key:
            raise RuntimeError("Snapshot function not detected")

        hash_parts: list[str] = []
        for content in contents:
            for part in content.parts:
                if part.inline_data:
                    hash_parts.append(part.inline_data[1])
                if part.text:
                    hash_parts.append(str(part.text))
        content_hash = sha256(" ".join(hash_parts).encode("utf-8")).hexdigest()

        await page.evaluate(
            """
            ((hash) => {
                const dms = window.default_MakerSuite;
                const service = window.__bg_service;
                const snapKey = window.__snap_key;
                if (!dms || !service || !snapKey || typeof dms[snapKey] !== 'function') {
                    window.__sr = '';
                    window.__sl = 0;
                    window.__snap_error = 'service_unavailable';
                    return;
                }
                window.__sr = '';
                window.__sl = 0;
                window.__snap_error = '';
                const result = dms[snapKey](service, hash);
                if (result instanceof Promise) {
                    result.then((snapshot) => {
                        window.__sr = snapshot || '';
                        window.__sl = snapshot ? snapshot.length : 0;
                    }).catch((error) => {
                        window.__snap_error = String(error);
                    });
                    return;
                }
                window.__sr = result || '';
                window.__sl = result ? result.length : 0;
            })(%s)
            """
            % json.dumps(content_hash)
        )

        for _ in range(20):
            length = await page.evaluate("() => (window.__sl || 0)")
            if length and length > 0:
                break
            await page.wait_for_timeout(500)

        snapshot = await page.evaluate("() => window.__sr")
        if snapshot:
            return snapshot
        error = await page.evaluate("() => window.__snap_error || ''")
        raise RuntimeError(f"Snapshot generation failed: {error or 'unknown'}")

    async def upload_images(self, image_paths: list[str]) -> list[str]:
        """Upload images via Google Drive API using page session credentials."""
        if not image_paths:
            return []

        page = await self.ensure_botguard_service()
        cookies = await page.get_cookies()
        return await self._upload_images_via_api(image_paths, cookies)

    async def _upload_images_via_api(self, image_paths: list[str], cookies: list[dict[str, Any]]) -> list[str]:
        """Upload images through HTTP requests carrying the browser cookies."""
        import httpx

        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if "google" in c.get("domain", ""))
        uploaded_ids: list[str] = []

        headers = {
            "Cookie": cookie_header,
            "Origin": "https://aistudio.google.com",
            "Referer": "https://aistudio.google.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            for path_str in image_paths:
                p = Path(path_str)
                if not p.exists():
                    continue
                file_bytes = p.read_bytes()
                mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                upload_url = "https://content.googleapis.com/upload/drive/v3/files?uploadType=media"
                upload_headers = dict(headers)
                upload_headers["Content-Type"] = mime
                resp = await client.post(upload_url, headers=upload_headers, content=file_bytes)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    file_id = data.get("id")
                    if file_id:
                        uploaded_ids.append(file_id)

        if len(uploaded_ids) != len(image_paths):
            log.warning("Partial image upload: expected %d, got %d", len(image_paths), len(uploaded_ids))
        return uploaded_ids

    async def send_hooked_request(self, *, body: str, timeout_ms: int) -> tuple[int, bytes]:
        """Replay request via XHR inside the browser context."""
        page = await self.ensure_botguard_service()
        captured_url, captured_headers = self._get_captured_info()

        timeout_s = timeout_ms / 1000
        result = await page.evaluate(
            """(args) => {
                return new Promise((resolve) => {
                    var xhr = new XMLHttpRequest();
                    xhr.open('POST', args.url);
                    var h = args.headers;
                    for (var k in h) {
                        xhr.setRequestHeader(k, h[k]);
                    }
                    xhr.withCredentials = true;
                    xhr.timeout = args.timeout * 1000;
                    xhr.onload = function() {
                        resolve({status: xhr.status, body: xhr.responseText});
                    };
                    xhr.onerror = function() {
                        resolve({status: 0, body: 'network error'});
                    };
                    xhr.ontimeout = function() {
                        resolve({status: 0, body: 'timeout'});
                    };
                    xhr.send(args.body);
                });
            }""",
            {
                "url": captured_url,
                "headers": captured_headers,
                "body": body,
                "timeout": timeout_s,
            },
        )

        status = result.get("status", 0) if result else 0
        raw_text = result.get("body", "") if result else ""
        if status == 0:
            raise RuntimeError(f"replay failed: {raw_text}")
        return status, raw_text.encode("utf-8")

    async def send_streaming_request(
        self,
        *,
        body: str,
        timeout_ms: int,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Send a streaming request, yielding ('status', int) and ('chunk', bytes) events."""
        page, captured_url, captured_headers = await self._prepare_streaming()
        timeout_s = timeout_ms / 1000
        rid = uuid.uuid4().hex[:8]

        await page.evaluate(
            STREAMING_INIT_JS,
            {
                "url": captured_url,
                "headers": captured_headers,
                "body": body,
                "timeout": timeout_s,
                "rid": rid,
            },
        )

        deadline = asyncio.get_running_loop().time() + timeout_s
        status_sent = False

        try:
            while asyncio.get_running_loop().time() < deadline:
                event = await page.evaluate("(rid) => window.__stream_next[rid](250)", rid)
                if not event:
                    await asyncio.sleep(0.05)
                    continue

                event_type = event.get("type")
                if event_type == "idle":
                    continue
                if event_type == "status":
                    status = event.get("status", 0)
                    yield ("status", status)
                    status_sent = True
                    continue
                if event_type == "chunk":
                    text = event.get("text") or ""
                    if text:
                        yield ("chunk", text.encode("utf-8"))
                    continue
                if event_type == "error":
                    message = event.get("message", "unknown error")
                    raise RuntimeError(f"streaming request failed: {message}")
                if event_type in ("done", "aborted"):
                    break

            if not status_sent:
                raise RuntimeError("streaming request timeout: no response status")
        finally:
            try:
                await page.evaluate(
                    "(rid) => { if (window.__stream_abort && window.__stream_abort[rid]) window.__stream_abort[rid](); }",
                    rid,
                )
            except Exception:
                pass

    async def close(self) -> None:
        """Close browser session and free resources."""
        async with self._lock:
            await self._close_internal()

    async def _close_internal(self) -> None:
        if self._cdp_client is not None:
            try:
                await self._cdp_client.close()
            except Exception:
                pass
            self._cdp_client = None

        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

        self._page = None
        self._snap_key = None
        self._templates.clear()
        self._bootstrap_template = None

    async def _ensure_browser_cdp(self) -> CDPPage:
        """Launch Chromium subprocess and connect async CDP client."""
        profile_dir = self._profile_dir
        should_seed_from_auth = True
        if profile_dir:
            profile_path = Path(profile_dir)
            should_seed_from_auth = not (profile_path.exists() and any(profile_path.iterdir()))
            profile_path.mkdir(parents=True, exist_ok=True)

        self._proc = launch_chromium_process(
            port=self.port,
            user_data_dir=profile_dir,
            headless=settings.browser_headless,
        )

        self._cdp_client = CDPClient(port=self.port)
        self._page = await self._cdp_client.connect_page(block_assets=True)

        if should_seed_from_auth and self._auth_file and Path(self._auth_file).exists():
            try:
                data = json.loads(Path(self._auth_file).read_text(encoding="utf-8"))
                cached = data.get("cookies") or []
                if cached:
                    await self._page.set_cookies(cached)
                    await self._bootstrap_google_session(self._page)
                    if "accounts.google.com" not in (self._page.url or ""):
                        log.info("[chromium-auth] auth.json seeded context (%d cookies)", len(cached))
                        await self._save_cookies()
                        await self._goto_aistudio(self._page)
                        await self._install_hooks(self._page)
                        return self._page
            except Exception as e:
                log.debug("[chromium-auth] auth.json load failed: %s", e)

        await self._goto_aistudio(self._page)
        await self._install_hooks(self._page)
        return self._page

    async def _prepare_streaming(self) -> tuple[CDPPage, str, dict[str, str]]:
        page = await self.ensure_botguard_service()
        if not self._templates:
            from aistudio_api.config import DEFAULT_TEXT_MODEL

            try:
                await self.capture_template(DEFAULT_TEXT_MODEL)
            except Exception as e:
                log.warning("auto template capture failed: %s", e)
        url, headers = self._get_captured_info()
        return page, url, headers

    def _get_captured_info(self) -> tuple[str, dict[str, str]]:
        for tpl in self._templates.values():
            if tpl.get("url"):
                url = tpl["url"]
                headers = {
                    k: v
                    for k, v in tpl.get("headers", {}).items()
                    if k.lower() not in ("host", "content-length")
                }
                return url, headers
        raise RuntimeError("no captured URL available for replay")

    async def _bootstrap_google_session(self, page: CDPPage) -> None:
        await page.goto(GOOGLE_LOGIN_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout_s=30.0)
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

    async def _goto_aistudio(self, page: CDPPage) -> None:
        last_exc = None
        for url in (AI_STUDIO_URL, AI_STUDIO_URL_FALLBACK):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout_s=20.0)
                current_url = page.url or ""
                if "available-regions" in current_url:
                    raise RuntimeError(f"Google AI Studio 地区限制 (IP 漏了/不支持): {current_url}")
                if "accounts.google.com" in current_url and "signin" in current_url:
                    raise RuntimeError(
                        f"Cookie 认证失败，已被重定向到 Google 登录页。 (url={current_url})"
                    )

                try:
                    await page.wait_for_selector("textarea", timeout_s=15.0)
                except Exception as wait_err:
                    now_url = page.url or ""
                    if "available-regions" in now_url:
                        raise RuntimeError(f"Google AI Studio 地区限制 (代理IP漏了/地区不支持): {now_url}") from None
                    raise wait_err
                try:
                    await page.evaluate(DIALOG_CLEANUP_JS)
                except Exception:
                    pass

                for _ in range(15):
                    try:
                        if await page.evaluate("() => !!window.default_MakerSuite"):
                            break
                    except Exception:
                        pass
                    await page.wait_for_timeout(500)

                await self._verify_account_identity(page)
                await self._save_cookies()
                return
            except Exception as exc:
                if "地区限制" in str(exc) or "Cookie 认证失败" in str(exc):
                    raise exc
                log.debug("goto %s failed: %s", url, exc)
                last_exc = exc
        if last_exc is not None:
            raise last_exc

    async def _install_hooks(self, page: CDPPage) -> None:
        result = await page.evaluate(INSTALL_HOOKS_JS)
        if result == "already_hooked":
            return
        if isinstance(result, str) and result.startswith("hooked:"):
            self._snap_key = result.split(":", 1)[1]
            return
        for _ in range(3):
            await page.wait_for_timeout(2000)
            result = await page.evaluate(INSTALL_HOOKS_JS)
            if result == "already_hooked":
                return
            if isinstance(result, str) and result.startswith("hooked:"):
                self._snap_key = result.split(":", 1)[1]
                return
        page_url = page.url if page else "(no page)"
        page_title = await page.title() if page else ""
        raise RuntimeError(f"Hook install failed: {result} (url={page_url}, title={page_title!r})")

    async def _click_run_button(self, page: CDPPage) -> bool:
        if await page.send_control_enter("textarea"):
            return True

        if await page.click("button.ctrl-enter-submits"):
            return True
        if await page.click("button:has-text('Run')"):
            return True
        return await page.click("button:has(mat-icon)")

    async def _has_run_button(self, page: CDPPage) -> bool:
        try:
            has_stop = await page.query_selector("button:has-text('Stop')")
            if has_stop:
                return False
            if await page.query_selector("button.ctrl-enter-submits"):
                return True
            return await page.query_selector("button:has-text('Run')")
        except Exception:
            return False

    async def _wait_until_idle(self, page: CDPPage) -> None:
        for _ in range(60):
            if await self._has_run_button(page):
                return
            await page.wait_for_timeout(1000)
        raise RuntimeError("page never became idle")

    async def _verify_account_identity(self, page: CDPPage) -> None:
        auth_file = self._auth_file
        if not auth_file:
            return
        meta_path = Path(auth_file).parent / "meta.json"
        if not meta_path.exists():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return
        expected_email = meta.get("email") or ""
        if not expected_email:
            return

        try:
            page_html = await page.content()
        except Exception:
            return

        if expected_email in page_html:
            return

        account_id = meta.get("id", "unknown")
        log.warning(
            "[account-guard] 页面未登录期望账号 %s (%s)，拒绝保存 cookies 以防交叉污染",
            expected_email,
            account_id,
        )
        if self._profile_dir:
            profile_path = Path(self._profile_dir)
            if profile_path.exists():
                log.warning("[account-guard] 删除被污染的 profile 目录: %s", profile_path)
                shutil.rmtree(profile_path, ignore_errors=True)
        raise RuntimeError(
            f"页面未登录期望的账号 {expected_email} ({account_id})，"
            f"已删除 profile 缓存，请重新导入该账号的 cookies"
        )

    async def _save_cookies(
        self,
        *,
        auth_file: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
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
            auth_path.write_text(json.dumps({"cookies": current_cookies, "origins": origins}, indent=2))
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
