"""XHR replay and streaming event collection transport layer."""

from __future__ import annotations

import asyncio
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
            while asyncio.get_running_loop().time() < deadline:
                raw_event = await page.evaluate(STREAM_POLL_JS, rid)
                if not raw_event or not isinstance(raw_event, dict):
                    await asyncio.sleep(0.05)
                    continue

                event_type = str(raw_event.get("type") or "")
                events_to_process: list[dict[str, object]] = []
                if event_type == "batch":
                    raw_list = raw_event.get("events")
                    if isinstance(raw_list, list):
                        events_to_process = [e for e in raw_list if isinstance(e, dict)]
                else:
                    events_to_process = [raw_event]

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
            if not is_terminal:
                raise TimeoutError("streaming response timed out before completion")
        finally:
            with suppress(Exception):
                await page.evaluate(STREAM_CLEANUP_JS, rid)
