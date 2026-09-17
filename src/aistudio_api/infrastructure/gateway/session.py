"""Shared browser session management for gateway operations.

Uses native async CDP engine for zero-Node, fast and memory-efficient
Chromium automation with kernel-level asset pruning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
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
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent

log = logging.getLogger("aistudio.session")

AI_STUDIO_URL = "https://aistudio.google.com/prompts/new_chat?model=gemini-3.7-flash"
AI_STUDIO_URL_FALLBACK = "https://aistudio.google.com/app/prompts/new_chat"
GOOGLE_LOGIN_BOOTSTRAP_URL = (
    "https://accounts.google.com/ServiceLogin?continue=https://aistudio.google.com"
)

INSTALL_HOOKS_JS = r"""
((() => {
    if (window.__bg_hooked && window.__snap_key) return 'already_hooked';

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

    // Hook snapshot function to capture service
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

    window.__bg_hooked = true;
    window.__snap_key = snapKey;
    return 'hooked:' + snapKey;
})())
"""

DIALOG_CLEANUP_JS = """(() => {
    const cookieBtn = document.querySelector('.glue-cookie-notification-bar__accept');
    if (cookieBtn) { try { cookieBtn.click(); } catch(e) {} }
    document.querySelectorAll('button, mat-card, a, [role="button"]').forEach((el) => {
        const text = (el.textContent || el.innerText || '').trim().toLowerCase();
        if (['dismiss', 'close', 'accept', 'ok', 'agree', 'got it', 'start building', 'code and chat'].some(w => text.includes(w))) {
            try { el.click(); } catch(e) {}
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
        if (state.events.length) {
            var batch = state.events.slice();
            state.events.length = 0;
            return Promise.resolve({type: 'batch', events: batch});
        }
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
                if (state.events.length) {
                    var batch = [event].concat(state.events);
                    state.events.length = 0;
                    resolve({type: 'batch', events: batch});
                } else {
                    resolve(event);
                }
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
    var h = args.headers || {};
    for (var k in h) {
        if (k.toLowerCase() === 'authorization') continue;
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
    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return m ? decodeURIComponent(m[1]) : '';
    }
    var sapisid = getCookie('SAPISID') || getCookie('__Secure-1PAPISID') || getCookie('__Secure-3PAPISID');
    if (sapisid && window.crypto && window.crypto.subtle) {
        var sapisid1p = getCookie('__Secure-1PAPISID') || sapisid;
        var sapisid3p = getCookie('__Secure-3PAPISID') || sapisid;
        var ts = Math.floor(Date.now() / 1000);
        var origin = 'https://aistudio.google.com';
        function sha1(str) {
            return crypto.subtle.digest('SHA-1', new TextEncoder().encode(str)).then(function(buf) {
                return Array.from(new Uint8Array(buf)).map(function(b) { return b.toString(16).padStart(2, '0'); }).join('');
            });
        }
        Promise.all([
            sha1(ts + ' ' + sapisid + ' ' + origin),
            sha1(ts + ' ' + sapisid1p + ' ' + origin),
            sha1(ts + ' ' + sapisid3p + ' ' + origin)
        ]).then(function(res) {
            var auth = 'SAPISIDHASH ' + ts + '_' + res[0] + ' SAPISID1PHASH ' + ts + '_' + res[1] + ' SAPISID3PHASH ' + ts + '_' + res[2];
            xhr.setRequestHeader('Authorization', auth);
            xhr.send(args.body);
        }).catch(function() {
            var fallbackAuth = h['Authorization'] || h['authorization'];
            if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
            xhr.send(args.body);
        });
    } else {
        var fallbackAuth = h['Authorization'] || h['authorization'];
        if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
        xhr.send(args.body);
    }
}"""

BOTGUARD_BOOTSTRAP_PROMPT = "say '1'"
TEMPLATE_CAPTURE_PROMPT = "say 't'"

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
        self._templates: dict[str, dict[str, object]] = {}
        self._bootstrap_template: dict[str, object] | None = dict(DEFAULT_BOOTSTRAP_TEMPLATE)
        self._lock = asyncio.Lock()
        self._botguard_lock = asyncio.Lock()
        self._template_lock = asyncio.Lock()
        self._in_flight: int = 0
        self._switching: bool = False
        self._switch_event = asyncio.Event()
        self._switch_event.set()

    @asynccontextmanager
    async def request_scope(self):
        """追踪正在进行的请求，防止切号时进程被强杀造成断流。"""
        await self._switch_event.wait()
        self._in_flight += 1
        try:
            yield
        finally:
            self._in_flight = max(0, self._in_flight - 1)

    async def ensure_context(self) -> CDPPage:
        """Ensure Chromium process is running and CDPPage is connected."""
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page

            await self._close_internal()
            return await self._ensure_browser_cdp()

    async def switch_auth(self, auth_file: str | None) -> None:
        """Switch active auth file and invalidate browser profile/templates with request draining."""
        async with self._lock:
            self._switch_event.clear()
            self._switching = True
            try:
                # 等待正在处理的请求排干，最多等待 15 秒，避免直接切号杀进程导致进行中的流断连
                # 但也不能无期限等待，否则会造成所有新请求排队超时
                for _ in range(150):
                    if self._in_flight <= 0:
                        break
                    await asyncio.sleep(0.1)

                self._auth_file = auth_file
                self._profile_dir = self._derive_profile_dir(auth_file)
                self._templates.clear()
                self._bootstrap_template = None
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

    async def ensure_botguard_service(self) -> CDPPage:
        """Ensure BotGuardService is captured in page context."""
        page = await self.ensure_context()
        if "aistudio.google.com" not in (page.url or ""):
            await self._goto_aistudio(page)
        await self._install_hooks(page)

        if await page.evaluate("() => !!window.__bg_service"):
            return page

        async with self._botguard_lock:
            if await page.evaluate("() => !!window.__bg_service"):
                return page

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
                    await page.wait_for_selector("textarea", timeout_s=20.0)
                except Exception as err:
                    dbg_url = page.url
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
                    (await page.evaluate("() => document.querySelector('textarea')?.value || ''")) or ""
                )
                await page.fill("textarea", BOTGUARD_BOOTSTRAP_PROMPT)
                await page.wait_for_timeout(800)
                await page.evaluate(DIALOG_CLEANUP_JS)

                if not await self._click_run_button(page):
                    raise RuntimeError(
                        "failed to trigger send while capturing BotGuardService"
                    )

                for i in range(45):
                    await page.wait_for_timeout(1000)
                    if await page.evaluate("() => !!window.__bg_service"):
                        await self._wait_until_idle(page)
                        if captured and self._bootstrap_template is None:
                            self._bootstrap_template = dict(captured)
                        await page.fill("textarea", original_text)
                        log.debug(
                            f"[timing] botguard captured after {i + 1}s, total {time.time() - t0:.1f}s"
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
            log.warning("[import_cookies] browser visit failed: %s", e)

        try:
            browser_cookies = await page.get_cookies()
            if browser_cookies:
                await self._save_cookies(
                    auth_file=target_auth_file, cookies=browser_cookies
                )
                log.info(
                    "[import_cookies] exported %d cookies from browser",
                    len(browser_cookies),
                )
            else:
                await self._save_cookies(auth_file=target_auth_file, cookies=pw_cookies)
            return len(browser_cookies or pw_cookies)
        finally:
            if switched_target and original_auth_file:
                await self.switch_auth(original_auth_file)
                self._profile_dir = original_profile_dir

    async def capture_template(self, model: str) -> dict[str, object]:
        """Capture GenerateContent request headers and URL template for the given model."""
        if model in self._templates:
            return self._templates[model]

        async with self._template_lock:
            if model in self._templates:
                return self._templates[model]

            page = await self.ensure_botguard_service()
            if self._bootstrap_template:
                bootstrap = dict(self._bootstrap_template)
                self._templates[model] = bootstrap
                return bootstrap
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
                (await page.evaluate("() => document.querySelector('textarea')?.value || ''")) or ""
            )
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
            with suppress(Exception):
                await page.fill("textarea", original_text)

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

        # 直接使用 Promise 求值，完全隔离每个并发调用的结果，避免污染 window 全局变量
        script = """
        async (hash) => {
            const dms = window.default_MakerSuite;
            const service = window.__bg_service;
            const snapKey = window.__snap_key;
            if (!dms || !service || !snapKey || typeof dms[snapKey] !== 'function') {
                throw new Error('service_unavailable');
            }
            const result = dms[snapKey](service, hash);
            const snapshot = await Promise.resolve(result);
            if (!snapshot || typeof snapshot !== 'string') {
                throw new Error('empty_snapshot');
            }
            return snapshot;
        }
        """
        for attempt in range(3):
            try:
                snapshot = await page.evaluate(
                    script, args=content_hash, timeout_s=10.0
                )
                if snapshot and isinstance(snapshot, str) and len(snapshot) > 0:
                    return snapshot
            except Exception as e:
                log.debug(
                    "Async evaluate snapshot attempt %d failed: %s", attempt + 1, e
                )
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
            if url and headers:
                captured_url = url
                captured_headers = {
                    k: v
                    for k, v in headers.items()
                    if k.lower() not in ("host", "content-length")
                }
            else:
                captured_url, captured_headers = self._get_captured_info()

            timeout_s = timeout_ms / 1000
            result = await page.evaluate(
                """(args) => {
                    return new Promise((resolve) => {
                        var xhr = new XMLHttpRequest();
                        xhr.open('POST', args.url);
                        var h = args.headers || {};
                        for (var k in h) {
                            if (k.toLowerCase() === 'authorization') continue;
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
                        function getCookie(name) {
                            var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
                            return m ? decodeURIComponent(m[1]) : '';
                        }
                        var sapisid = getCookie('SAPISID') || getCookie('__Secure-1PAPISID') || getCookie('__Secure-3PAPISID');
                        if (sapisid && window.crypto && window.crypto.subtle) {
                            var sapisid1p = getCookie('__Secure-1PAPISID') || sapisid;
                            var sapisid3p = getCookie('__Secure-3PAPISID') || sapisid;
                            var ts = Math.floor(Date.now() / 1000);
                            var origin = 'https://aistudio.google.com';
                            function sha1(str) {
                                return crypto.subtle.digest('SHA-1', new TextEncoder().encode(str)).then(function(buf) {
                                    return Array.from(new Uint8Array(buf)).map(function(b) { return b.toString(16).padStart(2, '0'); }).join('');
                                });
                            }
                            Promise.all([
                                sha1(ts + ' ' + sapisid + ' ' + origin),
                                sha1(ts + ' ' + sapisid1p + ' ' + origin),
                                sha1(ts + ' ' + sapisid3p + ' ' + origin)
                            ]).then(function(res) {
                                var auth = 'SAPISIDHASH ' + ts + '_' + res[0] + ' SAPISID1PHASH ' + ts + '_' + res[1] + ' SAPISID3PHASH ' + ts + '_' + res[2];
                                xhr.setRequestHeader('Authorization', auth);
                                xhr.send(args.body);
                            }).catch(function() {
                                var fallbackAuth = h['Authorization'] || h['authorization'];
                                if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
                                xhr.send(args.body);
                            });
                        } else {
                            var fallbackAuth = h['Authorization'] || h['authorization'];
                            if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
                            xhr.send(args.body);
                        }
                    });
                }""",
                {
                    "url": captured_url,
                    "headers": captured_headers,
                    "body": body,
                    "timeout": timeout_s,
                },
            )

            res_dict: dict[str, object] = result if isinstance(result, dict) else {}
            status = int(str(res_dict.get("status") or 0))
            raw_text = str(res_dict.get("body") or "")
            if status == 0:
                raise RuntimeError(f"replay failed: {raw_text}")
            return status, raw_text.encode("utf-8")

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
            if url and headers:
                page = await self.ensure_botguard_service()
                captured_url = url
                captured_headers = {
                    k: v
                    for k, v in headers.items()
                    if k.lower() not in ("host", "content-length")
                }
            else:
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
                    raw_event = await page.evaluate(
                        "(rid) => window.__stream_next && window.__stream_next[rid] ? window.__stream_next[rid](250) : {type: 'error', message: 'stream_session_lost'}",
                        rid,
                    )
                    if not raw_event or not isinstance(raw_event, dict):
                        await asyncio.sleep(0.05)
                        continue

                    event_type = str(raw_event.get("type") or "")
                    events_to_process = []
                    if event_type == "batch":
                        raw_list = raw_event.get("events")
                        if isinstance(raw_list, list):
                            events_to_process = [e for e in raw_list if isinstance(e, dict)]
                    else:
                        events_to_process = [raw_event]

                    is_terminal = False
                    for event in events_to_process:
                        etype = str(event.get("type") or "")
                        if etype == "idle":
                            continue
                        if etype == "status":
                            status = int(str(event.get("status") or 0))
                            yield ("status", status)
                            status_sent = True
                            continue
                        if etype == "chunk":
                            text = str(event.get("text") or "")
                            if text:
                                yield ("chunk", text.encode("utf-8"))
                            continue
                        if etype == "error":
                            message = str(event.get("message") or "unknown error")
                            raise RuntimeError(f"streaming request failed: {message}")
                        if etype in ("done", "aborted"):
                            is_terminal = True
                            break
                    if is_terminal:
                        break
                if not status_sent:
                    raise RuntimeError("streaming request timeout: no response status")
            finally:
                with suppress(Exception):
                    await page.evaluate(
                        "(rid) => { if (window.__stream_abort && window.__stream_abort[rid]) window.__stream_abort[rid](); }",
                        rid,
                    )

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
        self._templates.clear()
        self._bootstrap_template = None

    async def _ensure_browser_cdp(self) -> CDPPage:
        """Launch Chromium subprocess and connect async CDP client."""
        profile_dir = self._profile_dir
        should_seed_from_auth = True
        if profile_dir:
            profile_path = Path(profile_dir)
            should_seed_from_auth = not (
                profile_path.exists() and any(profile_path.iterdir())
            )
            profile_path.mkdir(parents=True, exist_ok=True)

        self._proc = launch_chromium_process(
            port=self.port,
            user_data_dir=profile_dir,
            headless=settings.browser_headless,
        )

        self._cdp_client = CDPClient(port=self.port)
        self._page = await self._cdp_client.connect_page(block_assets=True)
        with suppress(Exception):
            tz_id = os.getenv("AISTUDIO_TIMEZONE", "Asia/Tokyo")
            await self._page.cdp.send(
                "Emulation.setTimezoneOverride", {"timezoneId": tz_id}
            )
            await self._page.cdp.send(
                "Emulation.setLocaleOverride", {"locale": "en-US"}
            )
        if should_seed_from_auth and self._auth_file and Path(self._auth_file).exists():
            try:
                data = json.loads(Path(self._auth_file).read_text(encoding="utf-8"))
                cached = data.get("cookies") or []
                if cached:
                    await self._page.set_cookies(cached)
                    await self._bootstrap_google_session(self._page)
                    if "accounts.google.com" not in (self._page.url or ""):
                        log.info(
                            "[chromium-auth] auth.json seeded context (%d cookies)",
                            len(cached),
                        )
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
            raw_url = tpl.get("url")
            if raw_url:
                url = str(raw_url)
                raw_headers = tpl.get("headers")
                headers_dict = raw_headers if isinstance(raw_headers, dict) else {}
                headers = {
                    str(k): str(v)
                    for k, v in headers_dict.items()
                    if str(k).lower() not in ("host", "content-length")
                }
                return url, headers
        raise RuntimeError("no captured URL available for replay")

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
        auth_user = "0"
        if self._auth_file:
            try:
                meta_path = Path(self._auth_file).parent / "meta.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    auth_user = str(meta.get("auth_user") or "0")
            except Exception:
                pass

        if auth_user and auth_user != "0":
            return [
                f"https://aistudio.google.com/u/{auth_user}/prompts/new_chat?model={model}",
                f"https://aistudio.google.com/u/{auth_user}/app/prompts/new_chat",
                AI_STUDIO_URL,
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
                        if "aistudio.google.com" in curr:
                            log.debug(
                                "page.goto encountered net::ERR_ABORTED but already on aistudio: %s",
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
                if "accounts.google.com" in current_url and ("signin" in current_url or "ServiceLogin" in current_url):
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
                log.warning(
                    "[account-guard] 删除被污染的 profile 目录: %s", profile_path
                )
                shutil.rmtree(profile_path, ignore_errors=True)
        raise RuntimeError(
            f"页面未登录期望的账号 {expected_email} ({account_id})，"
            f"已删除 profile 缓存，请重新导入该账号的 cookies"
        )

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
