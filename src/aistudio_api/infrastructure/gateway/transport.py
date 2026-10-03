"""XHR replay and streaming event collection transport layer."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import TYPE_CHECKING

from aistudio_api.infrastructure.account.cookie_parser import calculate_sapisid_hash
from aistudio_api.infrastructure.browser.scripts import (
    HOOKED_REQUEST_JS,
    STREAM_CLEANUP_JS,
    STREAMING_INIT_JS,
    build_hooked_request_args,
    build_streaming_init_args,
)

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("transport")


def _get_active_auth_cookies() -> dict[str, str] | None:
    """从当前活跃账号的 auth.json 读取凭据 Cookie 作为本地高速兜底。"""
    try:
        from aistudio_api.infrastructure.account.account_store import AccountStore

        store = AccountStore()
        active = store.get_active_account()
        if not active:
            return None
        auth_path = store.get_auth_path_optional(active.id, require_exists=True)
        if auth_path and auth_path.is_file():
            data = json.loads(auth_path.read_text(encoding="utf-8"))
            cookies = data.get("cookies") or []
            return {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in cookies
                if c.get("name")
            }
    except Exception:
        pass
    return None


async def _ensure_authorization_header(
    page: CDPPage, headers: dict[str, str], auth_user: str = "0"
) -> dict[str, str]:
    """Ensure fresh SAPISIDHASH Authorization and X-Goog-AuthUser header are populated in Python."""
    incoming_auth = ""
    for k, v in headers.items():
        if k.lower() == "authorization" and v:
            incoming_auth = str(v)
            break

    clean_headers = {
        k: v
        for k, v in headers.items()
        if k.lower()
        not in ("host", "content-length", "authorization", "x-goog-authuser")
    }
    clean_headers["X-Goog-AuthUser"] = str(auth_user or "0")

    # 1. 优先使用 page 上的内存缓存 Cookie，避免高并发下向 Chromium 密集发送重复的 Network.getCookies CDP 流量
    cookie_dict = getattr(page, "_cached_cookies", None)
    if not cookie_dict or not any("SAPISID" in k for k in cookie_dict):
        with suppress(Exception):
            await page.get_cookies()
            cookie_dict = getattr(page, "_cached_cookies", None)
    # 2. 如果 CDP 出现超时波动或未取到 SAPISID，回退读取活跃账号 auth.json 凭据
    if not cookie_dict or not any("SAPISID" in k for k in cookie_dict):
        disk_cookies = _get_active_auth_cookies()
        if disk_cookies and any("SAPISID" in k for k in disk_cookies):
            cookie_dict = disk_cookies
            page._cached_cookies = cookie_dict
    fresh_auth = calculate_sapisid_hash(cookie_dict) if cookie_dict else ""
    if fresh_auth:
        clean_headers["Authorization"] = fresh_auth
    elif incoming_auth:
        clean_headers["Authorization"] = incoming_auth

    return clean_headers


class XHRStreamTransport:
    """Handles in-browser XHR request execution, streaming event collection, and resource cleanup."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, object]]] = {}

    def abort_all_streams(self, reason: str = "account_switched") -> None:
        """Immediately abort all active in-flight streaming queues."""
        for q in list(self._queues.values()):
            with suppress(Exception):
                q.put_nowait({"type": "error", "message": reason})
    def _ensure_page_binding(self, page: CDPPage) -> None:
        if getattr(page, "has_stream_binding", False):
            return
        page.has_stream_binding = True

        def on_stream_push(payload_str: str) -> None:
            try:
                data = json.loads(payload_str)
                if isinstance(data, dict):
                    rid = str(data.get("rid") or "")
                    q = self._queues.get(rid)
                    if q is not None:
                        try:
                            q.put_nowait(data)
                        except asyncio.QueueFull:
                            logger.warning(
                                "流式反压: 队列已满 (rid=%s)",
                                rid,
                            )
            except Exception as e:
                logger.debug("分发流式推送数据失败: %s", e)

        if hasattr(page, "on_binding"):
            page.on_binding("__aistudio_stream_push__", on_stream_push)
        elif hasattr(page, "cdp") and hasattr(page.cdp, "on"):

            def listener(params: dict[str, object]) -> None:
                if params.get("name") == "__aistudio_stream_push__":
                    on_stream_push(str(params.get("payload") or ""))

            page.cdp.on("Runtime.bindingCalled", listener)

    async def send_hooked_request(
        self,
        page: CDPPage,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
        timeout_ms: int,
        auth_user: str = "0",
    ) -> tuple[int, bytes]:
        """Replay request via XHR inside the browser page context."""
        timeout_s = timeout_ms / 1000
        clean_headers = await _ensure_authorization_header(
            page, headers, auth_user=auth_user
        )
        args = build_hooked_request_args(
            url=url,
            headers=clean_headers,
            body=body,
            timeout_s=timeout_s,
        )

        result = await page.evaluate(HOOKED_REQUEST_JS, args, timeout_s=timeout_s + 5.0)
        res_dict: dict[str, object] = result if isinstance(result, dict) else {}
        status = int(str(res_dict.get("status") or 0))
        raw_text = str(res_dict.get("body") or "")
        if status == 0:
            raise RuntimeError(f"replay failed: {raw_text}")
        return status, raw_text.encode("utf-8")

    async def send_streaming_request(
        self,
        page: CDPPage,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
        timeout_ms: int,
        auth_user: str = "0",
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Send a streaming request, yielding ('status', int) and ('chunk', bytes) events.

        Guarantees V8 window cleanup on termination/timeout and prevents silent truncation.
        """
        timeout_s = timeout_ms / 1000
        clean_headers = await _ensure_authorization_header(
            page, headers, auth_user=auth_user
        )
        rid = uuid.uuid4().hex[:8]

        # Ensure native binding on page and register local async queue
        self._ensure_page_binding(page)
        if hasattr(page, "add_binding"):
            with suppress(Exception):
                await page.add_binding("__aistudio_stream_push__")
        elif hasattr(page, "cdp") and hasattr(page.cdp, "send"):
            with suppress(Exception):
                await page.cdp.send(
                    "Runtime.addBinding", {"name": "__aistudio_stream_push__"}
                )

        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._queues[rid] = queue

        init_args = build_streaming_init_args(
            url=url,
            headers=clean_headers,
            body=body,
            timeout_s=timeout_s,
            rid=rid,
        )
        await page.evaluate(STREAMING_INIT_JS, init_args)

        deadline = asyncio.get_running_loop().time() + timeout_s
        status_sent = False
        is_terminal = False

        try:
            while not is_terminal:
                if page.is_closed() is True:
                    raise RuntimeError("Browser page closed during streaming")
                now = asyncio.get_running_loop().time()
                remaining = deadline - now
                if remaining <= 0:
                    break

                step_timeout = min(remaining, 1.0)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=step_timeout)
                except TimeoutError:
                    if page.is_closed() is True:
                        raise RuntimeError(
                            "Browser page closed during streaming"
                        ) from None
                    continue
                etype = str(event.get("type") or "")
                if etype == "status":
                    status = int(str(event.get("status") or 0))
                    yield ("status", status)
                    status_sent = True
                    deadline = asyncio.get_running_loop().time() + timeout_s
                elif etype == "chunk":
                    text = str(event.get("text") or "")
                    if text:
                        yield ("chunk", text.encode("utf-8"))
                        deadline = asyncio.get_running_loop().time() + timeout_s
                elif etype == "error":
                    message = str(event.get("message") or "unknown error")
                    raise RuntimeError(f"streaming request failed: {message}")
                elif etype in ("done", "aborted"):
                    while not queue.empty():
                        try:
                            remaining_event = queue.get_nowait()
                            if str(remaining_event.get("type") or "") == "chunk":
                                r_text = str(remaining_event.get("text") or "")
                                if r_text:
                                    yield ("chunk", r_text.encode("utf-8"))
                        except asyncio.QueueEmpty:
                            break
                    is_terminal = True
                    break
            if not status_sent:
                raise RuntimeError("streaming request timeout: no response status")
            if not is_terminal:
                raise TimeoutError("streaming response timed out before completion")
        finally:
            self._queues.pop(rid, None)
            with suppress(Exception):
                await page.evaluate(STREAM_CLEANUP_JS, rid)
