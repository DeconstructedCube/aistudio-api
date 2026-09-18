"""XHR replay and streaming event collection transport layer."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import TYPE_CHECKING

from aistudio_api.infrastructure.browser.scripts import (
    HOOKED_REQUEST_JS,
    STREAM_CLEANUP_JS,
    STREAM_POLL_JS,
    STREAMING_INIT_JS,
    build_hooked_request_args,
    build_streaming_init_args,
)

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage

logger = logging.getLogger("aistudio.transport")


class XHRStreamTransport:
    """Handles in-browser XHR request execution, streaming event collection, and resource cleanup."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, object]]] = {}
        self._bound_page_ids: set[int] = set()

    def _ensure_page_binding(self, page: CDPPage) -> None:
        page_id = id(page)
        if page_id in self._bound_page_ids:
            return
        self._bound_page_ids.add(page_id)

        def on_stream_push(payload_str: str) -> None:
            try:
                data = json.loads(payload_str)
                if isinstance(data, dict):
                    rid = str(data.get("rid") or "")
                    q = self._queues.get(rid)
                    if q is not None:
                        q.put_nowait(data)
            except Exception as e:
                logger.debug("Failed to dispatch stream push payload: %s", e)

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
    ) -> tuple[int, bytes]:
        """Replay request via XHR inside the browser page context."""
        timeout_s = timeout_ms / 1000
        clean_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("host", "content-length")
        }
        args = build_hooked_request_args(
            url=url,
            headers=clean_headers,
            body=body,
            timeout_s=timeout_s,
        )

        result = await page.evaluate(HOOKED_REQUEST_JS, args)
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
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Send a streaming request, yielding ('status', int) and ('chunk', bytes) events.

        Guarantees V8 window cleanup on termination/timeout and prevents silent truncation.
        """
        timeout_s = timeout_ms / 1000
        clean_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("host", "content-length")
        }
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
                if not queue.empty():
                    event = queue.get_nowait()
                else:
                    now = asyncio.get_running_loop().time()
                    remaining = deadline - now
                    if remaining <= 0:
                        break

                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=min(remaining, 0.05)
                        )
                    except TimeoutError:
                        with suppress(Exception):
                            raw_event = await page.evaluate(STREAM_POLL_JS, rid)
                            if isinstance(raw_event, dict):
                                etype = str(raw_event.get("type") or "")
                                if etype == "batch":
                                    raw_events = raw_event.get("events")
                                    if isinstance(raw_events, list):
                                        for sub in raw_events:
                                            if isinstance(sub, dict):
                                                queue.put_nowait(sub)
                                elif etype not in ("", "idle"):
                                    queue.put_nowait(raw_event)
                        continue
                etype = str(event.get("type") or "")
                if etype == "status":
                    status = int(str(event.get("status") or 0))
                    yield ("status", status)
                    status_sent = True
                elif etype == "chunk":
                    text = str(event.get("text") or "")
                    if text:
                        yield ("chunk", text.encode("utf-8"))
                elif etype == "error":
                    message = str(event.get("message") or "unknown error")
                    raise RuntimeError(f"streaming request failed: {message}")
                elif etype in ("done", "aborted"):
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
