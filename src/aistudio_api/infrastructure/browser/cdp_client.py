"""Native Async Python Chrome DevTools Protocol (CDP) client.

Replaces Node.js / Playwright with a zero-Node, pure-Python async WebSocket client
directly communicating with Chromium over DevTools JSON-RPC protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable
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
    "*google-analytics.com*",
    "*googletagmanager.com*",
    "*play.google.com/log*",
    "*bat.bing.com*",
    "*adservice.google.com*",
    "*pagead2.googlesyndication.com*",
]


class CDPError(Exception):
    """Exception raised for CDP JSON-RPC errors."""

    def __init__(self, code: int | str, message: str, data: Any = None):
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
        self._futures: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._listeners: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}
        self._recv_task: asyncio.Task[None] | None = None
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
                                asyncio.create_task(res)
                        except Exception as e:
                            log.debug("Error in CDP event listener for %s: %s", method, e)

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
        params: dict[str, Any] | None = None,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Send a JSON-RPC command over CDP and await result."""
        if not self.ws or self._closed:
            raise RuntimeError("CDP connection is not open")

        req_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._futures[req_id] = fut

        payload = {
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        await self.ws.send(json.dumps(payload))

        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._futures.pop(req_id, None)
            raise TimeoutError(f"CDP command {method} timed out after {timeout_s}s") from None

    def on(self, event: str, callback: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """Register an event listener. Returns an unsubscribe function."""
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

        def unsubscribe() -> None:
            self.remove_listener(event, callback)

        return unsubscribe

    def remove_listener(self, event: str, callback: Callable[[dict[str, Any]], Any]) -> None:
        """Remove an event listener."""
        if event in self._listeners and callback in self._listeners[event]:
            self._listeners[event].remove(callback)

    async def close(self) -> None:
        """Close WebSocket connection cleanly."""
        self._closed = True
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None


class CDPPage:
    """Represents a single browser Page target controlled via CDP."""

    def __init__(self, cdp: CDPConnection, target_id: str):
        self.cdp = cdp
        self.target_id = target_id
        self._is_closed = False
        self._last_url: str = ""

    @property
    def url(self) -> str:
        return self._last_url

    def is_closed(self) -> bool:
        return self._is_closed or self.cdp._closed

    async def init_domains(self, block_assets: bool = True) -> None:
        """Enable required CDP domains and install kernel-level asset filters."""
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        await self.cdp.send("Network.enable")
        await self.cdp.send("DOM.enable")

        # Track navigation URLs
        def on_navigated(params: dict[str, Any]) -> None:
            frame = params.get("frame", {})
            if not frame.get("parentId"):  # Main frame
                self._last_url = frame.get("url", "")

        self.cdp.on("Page.frameNavigated", on_navigated)

        if block_assets:
            await self.set_blocked_urls(BLOCKED_URL_PATTERNS)

    async def set_blocked_urls(self, patterns: list[str]) -> None:
        """Block matching resource URLs inside Chromium's C++ network service."""
        try:
            await self.cdp.send("Network.setBlockedURLs", {"urls": patterns})
            log.debug("Configured Network.setBlockedURLs (%d patterns)", len(patterns))
        except Exception as e:
            log.warning("Failed to configure Network.setBlockedURLs: %s", e)

    async def evaluate(
        self,
        expression: str,
        args: Any = None,
        await_promise: bool = True,
        return_by_value: bool = True,
        timeout_s: float = 30.0,
    ) -> Any:
        """Evaluate a JS expression or function in the page's main world."""
        expr = expression.strip()
        if expr.startswith("mw:"):
            expr = expr[3:].strip()

        if args is not None:
            expr = f"({expr})({json.dumps(args)})"
        else:
            is_iife = bool(re.search(r"\)\s*\([^)]*\)\s*;?$", expr))
            if not is_iife:
                is_arrow = bool(re.match(r"^(?:async\s+)?(?:\([^()]*\)|[a-zA-Z_$][\w$]*)\s*=>", expr))
                is_fn = bool(re.match(r"^(?:async\s+)?function\b", expr))
                if is_arrow or is_fn:
                    expr = f"({expr})()"
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
            exc = res["exceptionDetails"]
            exc_text = exc.get("exception", {}).get("description") or exc.get("text", "JS exception")
            raise RuntimeError(f"CDP JS evaluation error: {exc_text}")

        result_obj = res.get("result", {})
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

        event_name = "Page.loadEventFired" if wait_until == "load" else "Page.domContentEventFired"

        def on_event(_params: dict[str, Any]) -> None:
            if not nav_done.done():
                nav_done.set_result(True)

        unsub = self.cdp.on(event_name, on_event)
        try:
            nav_res = await self.cdp.send("Page.navigate", {"url": url}, timeout_s=timeout_s)
            if "errorText" in nav_res:
                raise RuntimeError(f"Navigation failed: {nav_res['errorText']}")

            # Also update current URL
            self._last_url = url
            await asyncio.wait_for(nav_done, timeout=timeout_s)
        except asyncio.TimeoutError:
            # Timeout during DOM load is non-fatal for SPAs, update URL from location
            pass
        finally:
            unsub()

        # Update actual URL from window.location
        try:
            actual_url = await self.evaluate("() => window.location.href", timeout_s=5.0)
            if actual_url:
                self._last_url = actual_url
        except Exception:
            pass

    async def title(self) -> str:
        """Get document title."""
        try:
            return (await self.evaluate("() => document.title", timeout_s=5.0)) or ""
        except Exception:
            return ""

    async def content(self) -> str:
        """Get full HTML content of the page."""
        try:
            return (await self.evaluate("() => document.documentElement.outerHTML", timeout_s=10.0)) or ""
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

    async def get_cookies(self) -> list[dict[str, Any]]:
        """Retrieve cookies via CDP Network domain."""
        res = await self.cdp.send("Network.getCookies")
        return res.get("cookies", [])

    async def set_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Inject cookies via CDP Network domain."""
        if not cookies:
            return

        formatted: list[dict[str, Any]] = []
        for c in cookies:
            item: dict[str, Any] = {
                "name": str(c["name"]),
                "value": str(c["value"]),
                "path": str(c.get("path", "/")),
                "secure": bool(c.get("secure", True)),
                "httpOnly": bool(c.get("httpOnly", False)),
            }
            # Handle domain vs url
            domain = c.get("domain")
            url = c.get("url")
            if domain:
                if item["name"].startswith("__Host-"):
                    # Host-only cookies must not specify domain with leading dot
                    clean_domain = domain.lstrip(".")
                    item["url"] = f"https://{clean_domain}/"
                else:
                    item["domain"] = domain
            elif url:
                item["url"] = url
            else:
                item["domain"] = ".google.com"

            same_site = c.get("sameSite")
            if same_site:
                if same_site.lower() == "none":
                    item["sameSite"] = "None"
                elif same_site.lower() == "lax":
                    item["sameSite"] = "Lax"
                elif same_site.lower() == "strict":
                    item["sameSite"] = "Strict"

            if "expires" in c and c["expires"] is not None:
                item["expires"] = float(c["expires"])

            formatted.append(item)

        try:
            await self.cdp.send("Network.setCookies", {"cookies": formatted})
            log.debug("Injected %d cookies via CDP", len(formatted))
        except Exception as e:
            log.warning("Batch Network.setCookies failed (%s), retrying individually", e)
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

    def on_request(self, callback: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """Listen to outgoing HTTP requests."""
        def listener(params: dict[str, Any]) -> None:
            req = params.get("request", {})
            data = {
                "request_id": params.get("requestId"),
                "url": req.get("url", ""),
                "method": req.get("method", "GET"),
                "headers": req.get("headers", {}),
                "post_data": req.get("postData", ""),
            }
            callback(data)

        return self.cdp.on("Network.requestWillBeSent", listener)

    def on_response(self, callback: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """Listen to incoming HTTP responses."""
        def listener(params: dict[str, Any]) -> None:
            resp = params.get("response", {})
            data = {
                "request_id": params.get("requestId"),
                "url": resp.get("url", ""),
                "status": resp.get("status", 0),
                "headers": resp.get("headers", {}),
                "mime_type": resp.get("mimeType", ""),
            }
            callback(data)

        return self.cdp.on("Network.responseReceived", listener)

    async def get_response_body(self, request_id: str) -> str:
        """Get response body for a completed request."""
        try:
            res = await self.cdp.send("Network.getResponseBody", {"requestId": request_id})
            body = res.get("body", "")
            if res.get("base64Encoded"):
                import base64
                return base64.b64decode(body).decode("utf-8", errors="replace")
            return body
        except Exception as e:
            log.debug("get_response_body(%s) failed: %s", request_id, e)
            return ""

    async def close(self) -> None:
        """Close this page target."""
        self._is_closed = True
        try:
            await self.cdp.send("Page.close")
        except Exception:
            pass
        await self.cdp.close()


class CDPClient:
    """Manages connection and discovery of CDP targets on a Chromium debugging port."""

    def __init__(self, port: int, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.base_url = f"http://{host}:{port}"
        self.page: CDPPage | None = None

    async def get_version(self) -> dict[str, Any]:
        """Fetch /json/version info."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/json/version")
            resp.raise_for_status()
            return resp.json()

    async def get_targets(self) -> list[dict[str, Any]]:
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
        raise TimeoutError(f"Chromium CDP endpoint on port {self.port} not ready after {timeout_s}s: {last_err}")

    async def connect_page(self, block_assets: bool = True) -> CDPPage:
        """Connect to an existing page target or create one."""
        await self.wait_until_ready()
        targets = await self.get_targets()
        page_target = None
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                page_target = t
                break

        if not page_target:
            # Create new page target via /json/new
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.put(f"{self.base_url}/json/new")
                resp.raise_for_status()
                page_target = resp.json()

        ws_url = page_target["webSocketDebuggerUrl"]
        target_id = page_target.get("id", "")

        conn = CDPConnection(ws_url)
        await conn.connect()

        page = CDPPage(conn, target_id)
        await page.init_domains(block_assets=block_assets)
        self.page = page
        return page

    async def close(self) -> None:
        """Close active page and release resources."""
        if self.page:
            await self.page.close()
            self.page = None
