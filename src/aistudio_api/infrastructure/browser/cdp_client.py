"""Native Async Python Chrome DevTools Protocol (CDP) client.

Replaces Node.js / Playwright with a zero-Node, pure-Python async WebSocket client
directly communicating with Chromium over DevTools JSON-RPC protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Callable

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

log = logging.getLogger("aistudio.cdp")

BLOCKED_URL_PATTERNS: list[str] = [
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.webp",
    "*.gif",
    "*.svg",
    "*.ico",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.otf",
    "*.mp4",
    "*.webm",
    "*.mp3",
    "*.ogg",
    "*.wav",
    "*.flac",
    "*monaco-editor*",
    "*codemirror*",
    "*vs/editor*",
    "*vs/base*",
    "*mathjax*",
    "*katex*",
    "*google-analytics.com*",
    "*googletagmanager.com*",
    "*play.google.com/log*",
    "*bat.bing.com*",
    "*adservice.google.com*",
    "*pagead2.googlesyndication.com*",
    "*doubleclick*",
    "*doubleclick.net*",
    "*recaptcha*",
    "*google.com/recaptcha*",
]


class CDPError(Exception):
    """Exception raised for CDP JSON-RPC errors."""

    def __init__(self, code: int | str, message: str, data: object = None):
        super().__init__(f"CDP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class CDPConnection:
    """Manages an async WebSocket connection to a Chrome DevTools Protocol target."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws: ClientConnection | None = None
        self._next_id = 1
        self._futures: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._listeners: dict[str, list[Callable[[dict[str, object]], object]]] = {}
        self._recv_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    async def connect(self, timeout_s: float = 10.0) -> None:
        """Establish WebSocket connection and start receive loop."""
        self.ws = await asyncio.wait_for(
            websockets.connect(
                self.ws_url,
                max_size=None,
                ping_interval=20,
                ping_timeout=20,
            ),
            timeout=timeout_s,
        )
        self._closed = False
        self._recv_task = asyncio.create_task(
            self._recv_loop(),
            name=f"cdp_recv_{self.ws_url[-12:]}",
        )

    async def _recv_loop(self) -> None:
        """Background loop receiving and dispatching CDP messages."""
        try:
            while self.ws and not self._closed:
                raw = await self.ws.recv()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                req_id = msg.get("id")
                if req_id is not None and req_id in self._futures:
                    fut = self._futures.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            err = msg["error"]
                            fut.set_exception(
                                CDPError(
                                    err.get("code", -1),
                                    err.get("message", "unknown error"),
                                    err.get("data"),
                                )
                            )
                        else:
                            fut.set_result(msg.get("result", {}))

                method = msg.get("method")
                if method and method in self._listeners:
                    params = msg.get("params", {})
                    for cb in list(self._listeners[method]):
                        try:
                            res = cb(params)
                            if asyncio.iscoroutine(res):
                                task = asyncio.create_task(res)
                                self._background_tasks.add(task)
                                task.add_done_callback(self._background_tasks.discard)
                        except Exception as e:
                            log.debug(
                                "Error in CDP event listener for %s: %s", method, e
                            )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._closed:
                log.debug("CDP recv loop closed: %s", e)
        finally:
            # Reject pending futures
            for fut in self._futures.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("CDP connection closed"))
            self._futures.clear()

    async def send(
        self,
        method: str,
        params: dict[str, object] | None = None,
        timeout_s: float = 30.0,
    ) -> dict[str, object]:
        """Send a JSON-RPC command over CDP and await result."""
        if not self.ws or self._closed:
            raise RuntimeError("CDP connection is not open")

        req_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, object]] = loop.create_future()
        self._futures[req_id] = fut

        payload = {
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as e:
            self._closed = True
            self._futures.pop(req_id, None)
            raise RuntimeError(f"CDP connection closed: {e}") from e

        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            self._futures.pop(req_id, None)
            raise TimeoutError(
                f"CDP command {method} timed out after {timeout_s}s"
            ) from None

    def on(
        self, event: str, callback: Callable[[dict[str, object]], object]
    ) -> Callable[[], None]:
        """Register an event listener. Returns an unsubscribe function."""
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

        def unsubscribe() -> None:
            self.remove_listener(event, callback)

        return unsubscribe

    def remove_listener(
        self, event: str, callback: Callable[[dict[str, object]], object]
    ) -> None:
        """Remove an event listener."""
        if event in self._listeners and callback in self._listeners[event]:
            self._listeners[event].remove(callback)

    async def close(self) -> None:
        """Close WebSocket connection cleanly."""
        self._closed = True
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
            self.ws = None


class CDPPage:
    """Represents a single browser Page target controlled via CDP."""

    def __init__(self, cdp: CDPConnection, target_id: str):
        self.cdp = cdp
        self.target_id = target_id
        self._is_closed = False
        self._last_url: str = ""
        self._unsubscribers: list[Callable[[], None]] = []
        self.has_stream_binding: bool = False

    def _track_listener(self, unsub: Callable[[], None]) -> Callable[[], None]:
        self._unsubscribers.append(unsub)

        def wrapped() -> None:
            if unsub in self._unsubscribers:
                self._unsubscribers.remove(unsub)
            unsub()

        return wrapped

    @property
    def url(self) -> str:
        return self._last_url

    def is_closed(self) -> bool:
        return self._is_closed or self.cdp._closed

    async def is_alive(self, timeout_s: float = 1.0) -> bool:
        """Fast non-blocking probe to verify if the page target and WebSocket are responsive."""
        if self.is_closed():
            return False
        try:
            res = await self.cdp.send(
                "Runtime.evaluate",
                {"expression": "1", "returnByValue": True},
                timeout_s=timeout_s,
            )
            return bool(res and not res.get("exceptionDetails"))
        except Exception:
            return False

    async def init_domains(self, block_assets: bool = True) -> None:
        """Enable required CDP domains and install kernel-level asset filters."""
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        await self.cdp.send("Network.enable")
        await self.cdp.send("DOM.enable")
        # Install native stream push binding
        with contextlib.suppress(Exception):
            await self.add_binding("__aistudio_stream_push__")

        # Track navigation URLs
        def on_navigated(params: dict[str, object]) -> None:
            raw_frame = params.get("frame")
            frame = raw_frame if isinstance(raw_frame, dict) else {}
            if not frame.get("parentId"):  # Main frame
                self._last_url = str(frame.get("url") or "")

        self._track_listener(self.cdp.on("Page.frameNavigated", on_navigated))
        if block_assets:
            await self.set_blocked_urls(BLOCKED_URL_PATTERNS)

    async def set_blocked_urls(self, patterns: list[str]) -> None:
        """Block matching resource URLs inside Chromium's C++ network service."""
        try:
            await self.cdp.send("Network.setBlockedURLs", {"urls": patterns})
            log.debug("Configured Network.setBlockedURLs (%d patterns)", len(patterns))
        except Exception as e:
            log.warning("Failed to configure Network.setBlockedURLs: %s", e)

    async def add_binding(self, name: str) -> None:
        """Expose a global function in page JS that dispatches Runtime.bindingCalled events."""
        await self.cdp.send("Runtime.addBinding", {"name": name})

    def on_binding(
        self, name: str, callback: Callable[[str], object]
    ) -> Callable[[], None]:
        """Listen for Runtime.bindingCalled events for a specific binding name."""

        def listener(params: dict[str, object]) -> None:
            if params.get("name") == name:
                payload = str(params.get("payload") or "")
                callback(payload)

        return self._track_listener(self.cdp.on("Runtime.bindingCalled", listener))

    async def evaluate(
        self,
        expression: str,
        args: object = None,
        await_promise: bool = True,
        return_by_value: bool = True,
        timeout_s: float = 30.0,
    ) -> object:
        """Evaluate a JS expression or function in the page's main world."""
        expr = expression.strip()
        if expr.startswith("mw:"):
            expr = expr[3:].strip()

        if args is not None:
            clean_expr = expr.rstrip()
            while clean_expr.endswith(";"):
                clean_expr = clean_expr[:-1].rstrip()
            expr = f"({clean_expr})({json.dumps(args)})"
        else:
            is_iife = bool(re.search(r"\)\s*\([^)]*\)\s*\)*\s*;?$", expr))
            if not is_iife:
                is_arrow = bool(
                    re.match(
                        r"^(?:async\s+)?(?:\([^()]*\)|[a-zA-Z_$][\w$]*)\s*=>", expr
                    )
                )
                is_fn = bool(re.match(r"^(?:async\s+)?function\b", expr))
                if is_arrow or is_fn:
                    clean_expr = expr.rstrip()
                    while clean_expr.endswith(";"):
                        clean_expr = clean_expr[:-1].rstrip()
                    expr = f"({clean_expr})()"
        res = await self.cdp.send(
            "Runtime.evaluate",
            {
                "expression": expr,
                "awaitPromise": await_promise,
                "returnByValue": return_by_value,
                "includeCommandLineAPI": True,
            },
            timeout_s=timeout_s,
        )

        if "exceptionDetails" in res:
            raw_exc = res["exceptionDetails"]
            exc: dict[str, object] = raw_exc if isinstance(raw_exc, dict) else {}
            raw_inner = exc.get("exception")
            inner: dict[str, object] = raw_inner if isinstance(raw_inner, dict) else {}
            exc_text = str(
                inner.get("description") or exc.get("text") or "JS exception"
            )
            raise RuntimeError(f"CDP JS evaluation error: {exc_text}")

        raw_result_obj = res.get("result")
        result_obj: dict[str, object] = (
            raw_result_obj if isinstance(raw_result_obj, dict) else {}
        )
        val_type = result_obj.get("type")
        if val_type == "undefined":
            return None
        return result_obj.get("value")

    async def goto(
        self,
        url: str,
        timeout_s: float = 30.0,
        wait_until: str = "domcontentloaded",
    ) -> None:
        """Navigate to a URL and wait until DOM is loaded."""
        loop = asyncio.get_running_loop()
        nav_done: asyncio.Future[bool] = loop.create_future()

        event_name = (
            "Page.loadEventFired"
            if wait_until == "load"
            else "Page.domContentEventFired"
        )

        def on_event(_params: dict[str, object]) -> None:
            if not nav_done.done():
                nav_done.set_result(True)

        unsub = self.cdp.on(event_name, on_event)
        try:
            nav_res = await self.cdp.send(
                "Page.navigate", {"url": url}, timeout_s=timeout_s
            )
            if "errorText" in nav_res:
                raise RuntimeError(f"Navigation failed: {nav_res['errorText']}")

            # Also update current URL
            self._last_url = url
            await asyncio.wait_for(nav_done, timeout=timeout_s)
        except TimeoutError:
            # Timeout during DOM load is non-fatal for SPAs, update URL from location
            pass
        finally:
            unsub()
            # Update actual URL from window.location
            try:
                actual_url = await self.evaluate(
                    "() => window.location.href", timeout_s=5.0
                )
                if actual_url is not None:
                    self._last_url = str(actual_url)
            except Exception:
                pass

    async def title(self) -> str:
        """Get document title."""
        try:
            val = await self.evaluate("() => document.title", timeout_s=5.0)
            return str(val) if val is not None else ""
        except Exception:
            return ""

    async def content(self) -> str:
        """Get full HTML content of the page."""
        try:
            val = await self.evaluate(
                "() => document.documentElement.outerHTML", timeout_s=10.0
            )
            return str(val) if val is not None else ""
        except Exception:
            return ""

    async def wait_for_timeout(self, ms: float) -> None:
        """Wait for specified milliseconds."""
        await asyncio.sleep(ms / 1000.0)

    async def wait_for_selector(
        self,
        selector: str,
        timeout_s: float = 20.0,
        interval_s: float = 0.3,
    ) -> bool:
        """Wait until element matching selector is present in DOM."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        check_expr = f"""() => {{
            const sel = {json.dumps(selector)};
            if (sel.includes(':has-text(')) {{
                const m = sel.match(/^(.*?):has-text\\(['"](.*?)['"]\\)(.*)$/);
                if (m) {{
                    const tag = m[1].trim() || '*';
                    const text = m[2];
                    return Array.from(document.querySelectorAll(tag)).some(e => (e.textContent || '').includes(text));
                }}
            }}
            if (sel.startsWith('text=')) {{
                const text = sel.slice(5).trim();
                return Array.from(document.querySelectorAll('*')).some(e => (e.textContent || '').includes(text));
            }}
            return document.querySelector(sel) !== null;
        }}"""
        while asyncio.get_running_loop().time() < deadline:
            try:
                found = await self.evaluate(check_expr, timeout_s=5.0)
                if found:
                    return True
            except Exception:
                pass
            await asyncio.sleep(interval_s)
        raise TimeoutError(f"Selector {selector!r} not found within {timeout_s}s")

    async def query_selector(self, selector: str) -> bool:
        """Check if an element exists."""
        check_expr = f"""() => {{
            const sel = {json.dumps(selector)};
            if (sel.includes(':has-text(')) {{
                const m = sel.match(/^(.*?):has-text\\(['"](.*?)['"]\\)(.*)$/);
                if (m) {{
                    const tag = m[1].trim() || '*';
                    const text = m[2];
                    return Array.from(document.querySelectorAll(tag)).some(e => (e.textContent || '').includes(text));
                }}
            }}
            if (sel.startsWith('text=')) {{
                const text = sel.slice(5).trim();
                return Array.from(document.querySelectorAll('*')).some(e => (e.textContent || '').includes(text));
            }}
            return document.querySelector(sel) !== null;
        }}"""
        try:
            return bool(await self.evaluate(check_expr))
        except Exception:
            return False

    async def click(self, selector: str, timeout_s: float = 5.0) -> bool:
        """Click an element matching selector."""
        expr = f"""() => {{
            const sel = {json.dumps(selector)};
            let el = null;
            if (sel.includes(':has-text(')) {{
                const m = sel.match(/^(.*?):has-text\\(['"](.*?)['"]\\)(.*)$/);
                if (m) {{
                    const tag = m[1].trim() || '*';
                    const text = m[2];
                    el = Array.from(document.querySelectorAll(tag)).find(e => (e.textContent || '').includes(text)) || null;
                }}
            }} else if (sel.startsWith('text=')) {{
                const text = sel.slice(5).trim();
                el = Array.from(document.querySelectorAll('*')).find(e => (e.textContent || '').includes(text)) || null;
            }} else {{
                el = document.querySelector(sel);
            }}
            if (!el) return false;
            el.click();
            return true;
        }}"""
        return bool(await self.evaluate(expr, timeout_s=timeout_s))

    async def fill(self, selector: str, value: str) -> bool:
        """Fill an input or textarea element with value."""
        expr = r"""(args) => {
            const sel = args.selector;
            let el = null;
            if (sel.includes(':has-text(')) {
                const m = sel.match(/^(.*?):has-text\(['"](.*?)['"]\)(.*)$/);
                if (m) {
                    const tag = m[1].trim() || '*';
                    const text = m[2];
                    el = Array.from(document.querySelectorAll(tag)).find(e => (e.textContent || '').includes(text)) || null;
                }
            } else {
                el = document.querySelector(sel);
            }
            if (!el) return false;
            el.focus();
            el.value = args.value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }"""
        return bool(await self.evaluate(expr, {"selector": selector, "value": value}))

    async def send_control_enter(self, selector: str = "textarea") -> bool:
        """Focus target and dispatch Control+Enter shortcut."""
        expr = f"""() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (el) {{
                el.focus();
                const evDown = new KeyboardEvent('keydown', {{
                    key: 'Enter',
                    code: 'Enter',
                    keyCode: 13,
                    which: 13,
                    ctrlKey: true,
                    bubbles: true,
                    cancelable: true,
                }});
                el.dispatchEvent(evDown);
                const evUp = new KeyboardEvent('keyup', {{
                    key: 'Enter',
                    code: 'Enter',
                    keyCode: 13,
                    which: 13,
                    ctrlKey: true,
                    bubbles: true,
                    cancelable: true,
                }});
                el.dispatchEvent(evUp);
                return true;
            }}
            return false;
        }}"""
        try:
            # Also send raw Input.dispatchKeyEvent with Ctrl modifier for Chromium shortcut dispatch
            await self.evaluate(expr)
            await self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "rawKeyDown",
                    "modifiers": 2,  # Ctrl modifier
                    "windowsVirtualKeyCode": 13,
                    "code": "Enter",
                    "key": "Enter",
                    "text": "\r",
                    "unmodifiedText": "\r",
                },
            )
            await self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "modifiers": 2,
                    "windowsVirtualKeyCode": 13,
                    "code": "Enter",
                    "key": "Enter",
                },
            )
            return True
        except Exception as e:
            log.debug("send_control_enter failed: %s", e)
            return False

    async def get_cookies(self) -> list[dict[str, object]]:
        """Retrieve cookies via CDP Network domain."""
        res = await self.cdp.send("Network.getCookies")
        raw_cookies = res.get("cookies")
        return raw_cookies if isinstance(raw_cookies, list) else []

    async def set_cookies(
        self, cookies: list[dict[str, object]] | list[dict[str, str]]
    ) -> None:
        """Inject cookies via CDP Network domain."""
        if not cookies:
            return

        formatted: list[dict[str, object]] = []
        for c in cookies:
            c_name = str(c.get("name", ""))
            item: dict[str, object] = {
                "name": c_name,
                "value": str(c.get("value", "")),
                "path": str(c.get("path", "/")),
                "secure": bool(c.get("secure", True)),
                "httpOnly": bool(c.get("httpOnly", False)),
            }
            domain = c.get("domain")
            url = c.get("url")
            if domain is not None:
                dom_str = str(domain)
                if c_name.startswith("__Host-"):
                    # Host-only cookies must not specify domain with leading dot
                    clean_domain = dom_str.lstrip(".")
                    item["url"] = f"https://{clean_domain}/"
                else:
                    item["domain"] = dom_str
            elif url is not None:
                item["url"] = str(url)
            else:
                item["domain"] = ".google.com"

            same_site = c.get("sameSite")
            if same_site is not None:
                ss_str = str(same_site).lower()
                if ss_str == "none":
                    item["sameSite"] = "None"
                elif ss_str == "lax":
                    item["sameSite"] = "Lax"
                elif ss_str == "strict":
                    item["sameSite"] = "Strict"

            if "expires" in c and c["expires"] is not None:
                item["expires"] = float(str(c["expires"]))

            formatted.append(item)

        try:
            await self.cdp.send("Network.setCookies", {"cookies": formatted})
            log.debug("Injected %d cookies via CDP", len(formatted))
        except Exception as e:
            log.warning(
                "Batch Network.setCookies failed (%s), retrying individually", e
            )
            for item in formatted:
                try:
                    await self.cdp.send("Network.setCookies", {"cookies": [item]})
                except Exception as ind_e:
                    log.debug("Failed to set cookie %s: %s", item.get("name"), ind_e)

    async def clear_cookies(self) -> None:
        """Clear all browser cookies."""
        try:
            await self.cdp.send("Network.clearBrowserCookies")
        except Exception as e:
            log.debug("Network.clearBrowserCookies failed: %s", e)

    def on_request(
        self, callback: Callable[[dict[str, object]], object]
    ) -> Callable[[], None]:
        """Listen to outgoing HTTP requests."""

        def listener(params: dict[str, object]) -> None:
            raw_req = params.get("request")
            req: dict[str, object] = raw_req if isinstance(raw_req, dict) else {}
            data: dict[str, object] = {
                "request_id": params.get("requestId"),
                "url": str(req.get("url") or ""),
                "method": str(req.get("method") or "GET"),
                "headers": req.get("headers") or {},
                "post_data": str(req.get("postData") or ""),
            }
            callback(data)

        return self._track_listener(self.cdp.on("Network.requestWillBeSent", listener))

    def on_response(
        self, callback: Callable[[dict[str, object]], object]
    ) -> Callable[[], None]:
        """Listen to incoming HTTP responses."""

        def listener(params: dict[str, object]) -> None:
            raw_resp = params.get("response")
            resp: dict[str, object] = raw_resp if isinstance(raw_resp, dict) else {}
            data: dict[str, object] = {
                "request_id": params.get("requestId"),
                "url": str(resp.get("url") or ""),
                "status": int(str(resp.get("status") or 0)),
                "headers": resp.get("headers") or {},
                "mime_type": str(resp.get("mimeType") or ""),
            }
            callback(data)

        return self._track_listener(self.cdp.on("Network.responseReceived", listener))

    async def get_response_body(self, request_id: str) -> str:
        """Get response body for a completed request."""
        try:
            res = await self.cdp.send(
                "Network.getResponseBody", {"requestId": request_id}
            )
            body = str(res.get("body") or "")
            if res.get("base64Encoded"):
                import base64

                return base64.b64decode(body).decode("utf-8", errors="replace")
            return body
        except Exception as e:
            log.debug("get_response_body(%s) failed: %s", request_id, e)
            return ""

    async def collect_garbage(self) -> None:
        """Trigger V8 garbage collection via HeapProfiler or window.gc()."""
        try:
            await self.cdp.send("HeapProfiler.collectGarbage", timeout_s=5.0)
        except Exception:
            with contextlib.suppress(Exception):
                await self.evaluate(
                    "() => { if (typeof window.gc === 'function') window.gc(); }",
                    timeout_s=5.0,
                )

    async def close(self) -> None:
        """Close this page target and clear all registered listeners."""
        self._is_closed = True
        for unsub in list(self._unsubscribers):
            with contextlib.suppress(Exception):
                unsub()
        self._unsubscribers.clear()
        with contextlib.suppress(Exception):
            await self.cdp.send("Page.close")
        await self.cdp.close()


class CDPClient:
    """Manages connection and discovery of CDP targets on a Chromium debugging port."""

    def __init__(self, port: int, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.base_url = f"http://{host}:{port}"
        self.page: CDPPage | None = None

    async def get_version(self) -> dict[str, object]:
        """Fetch /json/version info."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/json/version")
            resp.raise_for_status()
            return resp.json()

    async def is_endpoint_alive(self, timeout_s: float = 1.0) -> bool:
        """Fast check to verify if Chromium's CDP HTTP port is responding."""
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.get(f"{self.base_url}/json/version")
                return resp.status_code == 200
        except Exception:
            return False

    async def get_targets(self) -> list[dict[str, object]]:
        """Fetch list of active targets via /json/list."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/json/list")
            resp.raise_for_status()
            return resp.json()

    async def wait_until_ready(self, timeout_s: float = 15.0) -> None:
        """Wait until Chromium's CDP endpoint responds to HTTP requests."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        last_err: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                await self.get_version()
                return
            except Exception as e:
                last_err = e
                await asyncio.sleep(0.2)
        raise TimeoutError(
            f"Chromium CDP endpoint on port {self.port} not ready after {timeout_s}s: {last_err}"
        )

    async def connect_page(self, block_assets: bool = True) -> CDPPage:
        """Connect to an existing page target or create one with auto-retry on transient drops."""
        await self.wait_until_ready()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                targets = await self.get_targets()
                page_target = None
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        page_target = t
                        break

                if not page_target:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.put(f"{self.base_url}/json/new")
                        resp.raise_for_status()
                        page_target = resp.json()

                ws_url = str(page_target.get("webSocketDebuggerUrl") or "")
                target_id = str(page_target.get("id") or "")

                conn = CDPConnection(ws_url)
                await conn.connect()

                page = CDPPage(conn, target_id)
                await page.init_domains(block_assets=block_assets)
                self.page = page
                return page
            except Exception as e:
                last_exc = e
                log.debug("connect_page attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(0.3)
        raise RuntimeError(
            f"Failed to connect page target after 3 attempts: {last_exc}"
        ) from last_exc

    async def close(self) -> None:
        """Close active page and release resources."""
        if self.page:
            await self.page.close()
            self.page = None
