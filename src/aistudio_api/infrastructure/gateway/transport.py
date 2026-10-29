"""XHR replay and streaming event collection transport layer."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from aistudio_api.domain.errors import BrowserTransportError
from aistudio_api.infrastructure.account.cookie_parser import (
    calculate_sapisid_hash,
    get_tier_header_value,
)
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


def _ensure_net_trace(page: CDPPage) -> None:
    """Subscribe to $rpc/ CDP network events to attribute fetch failures."""
    if getattr(page, "has_net_trace", False):
        return
    page.has_net_trace = True

    def on_request(data: dict[str, object]) -> None:
        url = str(data.get("url") or "")
        rid = str(data.get("request_id") or "")
        if "$rpc/" not in url or not rid:
            return
        page.rpc_net_requests[rid] = {"url": url, "sent_at": time.time()}
        while len(page.rpc_net_requests) > 32:
            page.rpc_net_requests.pop(next(iter(page.rpc_net_requests)))

    def on_response(data: dict[str, object]) -> None:
        entry = page.rpc_net_requests.get(str(data.get("request_id") or ""))
        if entry is None:
            return
        entry["resp_status"] = int(str(data.get("status") or 0))
        entry["resp_at"] = time.time()

    def on_loading_failed(params: dict[str, object]) -> None:
        entry = page.rpc_net_requests.get(str(params.get("requestId") or ""))
        if entry is None:
            return
        entry.update(
            {
                "error_text": str(params.get("errorText") or ""),
                "canceled": bool(params.get("canceled")),
                "blocked_reason": params.get("blockedReason"),
                "failed_at": time.time(),
            }
        )
        page.rpc_net_failures.append(dict(entry))
        del page.rpc_net_failures[:-8]

    with suppress(Exception):
        page.on_request(on_request)
        page.on_response(on_response)
        page.on_loading_failed(on_loading_failed)


def _log_rpc_net_evidence(page: CDPPage, where: str) -> None:
    """Log recent $rpc/ Network.loadingFailed evidence when in-page fetch fails."""
    failures = getattr(page, "rpc_net_failures", None) or []
    if not failures:
        logger.warning(
            "页面 fetch 传输失败 (%s)：无 Network.loadingFailed 记录（页面 JS 层中止或监听晚于请求）",
            where,
        )
        return
    now = time.time()
    parts = []
    for f in failures[-3:]:
        tail = str(f.get("url") or "").rsplit("/", 1)[-1][:80]
        failed_at = f.get("failed_at")
        age = now - failed_at if isinstance(failed_at, (int, float)) else -1.0
        parts.append(
            f"{tail} err={f.get('error_text') or '-'} canceled={bool(f.get('canceled'))} "
            f"blocked={f.get('blocked_reason') or '-'} resp={f.get('resp_status') or 'none'} "
            f"age={age:.1f}s"
        )
    logger.warning(
        "页面 fetch 传输失败 (%s)：CDP 网络层证据 %s",
        where,
        " | ".join(parts),
    )


def _transport_error(page: CDPPage, message: str) -> BrowserTransportError:
    exc = BrowserTransportError(message)
    exc.net_evidence = list(getattr(page, "rpc_net_failures", None) or [])[-8:]
    return exc


def _get_auth_cookies(auth_file: str | None = None) -> dict[str, str] | None:
    """Read fallback credentials from the page's bound auth_file or active account."""
    try:
        if auth_file:
            bound_path = Path(auth_file)
            if bound_path.is_file():
                data = json.loads(bound_path.read_text(encoding="utf-8"))
                cookies = data.get("cookies") or []
                return {
                    str(c.get("name") or ""): str(c.get("value") or "")
                    for c in cookies
                    if c.get("name")
                }
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


def _get_active_account_tier() -> str:
    """读取当前活跃账号的订阅权益等级 (Free / Pro / Ultra / Plus)。"""
    try:
        from aistudio_api.infrastructure.account.account_store import AccountStore

        store = AccountStore()
        active = store.get_active_account()
        if active and getattr(active, "tier", None):
            return str(active.tier)
    except Exception:
        pass
    return "Free"


async def _ensure_authorization_header(
    page: CDPPage,
    headers: dict[str, str],
    auth_user: str = "0",
    tier: str | None = None,
) -> dict[str, str]:
    """Ensure fresh SAPISIDHASH Authorization, X-Goog-AuthUser, and X-AIStudio-G1-Tier headers are populated."""
    incoming_auth = ""
    for k, v in headers.items():
        if k.lower() == "authorization" and v:
            incoming_auth = str(v)
            break

    clean_headers = {
        k: v
        for k, v in headers.items()
        if k.lower()
        not in (
            "host",
            "content-length",
            "authorization",
            "x-goog-authuser",
            "x-aistudio-g1-tier",
        )
    }
    clean_headers["X-Goog-AuthUser"] = str(auth_user or "0")
    effective_tier = tier if tier and tier != "Free" else _get_active_account_tier()
    tier_header = get_tier_header_value(effective_tier)
    if tier_header:
        clean_headers["X-AIStudio-G1-Tier"] = tier_header
    cookie_dict = getattr(page, "_cached_cookies", None)
    if not cookie_dict or not any("SAPISID" in k for k in cookie_dict):
        with suppress(Exception):
            await page.get_cookies()
            cookie_dict = getattr(page, "_cached_cookies", None)
    # 2. 如果 CDP 出现超时波动或未取到 SAPISID，优先从页面绑定的 auth.json 回退读取凭据
    if not cookie_dict or not any("SAPISID" in k for k in cookie_dict):
        disk_cookies = _get_auth_cookies(getattr(page, "account_auth_file", None))
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

    @property
    def in_flight_count(self) -> int:
        """Return number of currently active streaming requests."""
        return len(self._queues)

    def abort_stream(self, rid: str, reason: str = "stream_aborted") -> bool:
        """Abort a specific streaming request by rid if it is active."""
        q = self._queues.get(rid)
        if q is not None:
            try:
                q.put_nowait({"type": "error", "message": reason})
                return True
            except asyncio.QueueFull:
                logger.warning("Abort stream failed: queue full (rid=%s)", rid)
                return False
        return False

    def close(self, reason: str = "transport_closed") -> None:
        """Close transport and notify any remaining in-flight queues on process exit."""
        for q in list(self._queues.values()):
            with suppress(Exception):
                q.put_nowait({"type": "error", "message": reason})
        self._queues.clear()

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
        tier: str | None = None,
    ) -> tuple[int, bytes]:
        """Replay request via XHR inside the browser page context."""
        timeout_s = timeout_ms / 1000
        clean_headers = await _ensure_authorization_header(
            page, headers, auth_user=auth_user, tier=tier
        )
        _ensure_net_trace(page)
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
            _log_rpc_net_evidence(page, "replay")
            raise _transport_error(page, f"replay failed: {raw_text}")
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
        tier: str | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Send a streaming request, yielding ('status', int) and ('chunk', bytes) events.

        Guarantees V8 window cleanup on termination/timeout and prevents silent truncation.
        """
        timeout_s = timeout_ms / 1000
        clean_headers = await _ensure_authorization_header(
            page, headers, auth_user=auth_user, tier=tier
        )
        _ensure_net_trace(page)
        rid = uuid.uuid4().hex[:8]

        # Ensure native binding on page and register local async queue
        if not getattr(page, "has_stream_binding", False):
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
                    raise BrowserTransportError("Browser page closed during streaming")
                now = asyncio.get_running_loop().time()
                remaining = deadline - now
                if remaining <= 0:
                    break

                step_timeout = min(remaining, 1.0)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=step_timeout)
                except TimeoutError:
                    if page.is_closed() is True:
                        raise BrowserTransportError(
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
                    _log_rpc_net_evidence(page, "streaming")
                    raise _transport_error(page, f"streaming request failed: {message}")
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
                _log_rpc_net_evidence(page, "streaming-no-status")
                raise _transport_error(
                    page, "streaming request timeout: no response status"
                )
            if not is_terminal:
                raise TimeoutError("streaming response timed out before completion")
        finally:
            self._queues.pop(rid, None)
            if not page.is_closed():
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(
                        page.evaluate(STREAM_CLEANUP_JS, rid, timeout_s=5.0)
                    )
