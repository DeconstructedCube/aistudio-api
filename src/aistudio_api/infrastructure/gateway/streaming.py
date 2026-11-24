"""Streaming workflow for chat completions."""

from __future__ import annotations

import codecs
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing

from aistudio_api.domain.errors import (
    RequestError,
    SessionExpiredError,
    TransportError,
    classify_error,
    is_html_payload,
    is_login_redirect_payload,
)
from aistudio_api.domain.models import ThoughtSignatureEvent
from aistudio_api.infrastructure.gateway.client import PreparedRequest
from aistudio_api.infrastructure.gateway.model_defaults import is_gemini4_model
from aistudio_api.infrastructure.gateway.session import GatewaySession
from aistudio_api.infrastructure.gateway.stream_parser import (
    IncrementalJSONStreamParser,
)
from aistudio_api.infrastructure.gateway.wire_parser import (
    WIRE_RESPONSE_ID_INDEX,
    is_terminal_wire_finish_reason,
    is_upstream_policy_block_text,
    parse_chunk_usage,
    parse_response_chunk,
)
from aistudio_api.infrastructure.gateway.wire_spec import (
    WIRE_FINISH_REASON_CONTINUING,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("streaming")


def _summarize_error_body(raw_response: str, limit: int = 500) -> str:
    text = raw_response.strip()
    if not text:
        return ""

    try:
        payload = json.loads(text)
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except json.JSONDecodeError:
        compact = " ".join(text.split())

    if len(compact) > limit:
        return compact[:limit] + "..."
    return compact


class StreamingGateway:
    def __init__(self, session: GatewaySession | None = None):
        self._session = session

    async def stream_chat(
        self,
        *,
        request: PreparedRequest,
        model: str,
    ) -> AsyncGenerator[tuple[str, object | None], None]:
        if not request:
            raise ValueError("prepared request is required")
        if self._session is None:
            raise RuntimeError("gateway session is required for streaming")
        request_body = request.body
        logger.debug("开始流式解析: model=%s, body_len=%d", model, len(request_body))
        parser = IncrementalJSONStreamParser()
        utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        latest_usage: dict[str, object] | None = None
        latest_finish_reason: int | None = None
        latest_response_id: str | None = None
        raw_parts: list[str] = []
        head_sample: str = ""
        emitted_any_chunk = False
        has_received_chunks = False
        status_code = 0
        accumulated_text_parts: list[str] = []
        accumulated_text_len = 0

        def emit_parsed_chunk(
            parsed_chunk: list[object],
        ) -> list[tuple[str, object | None]]:
            nonlocal \
                latest_usage, \
                latest_finish_reason, \
                latest_response_id, \
                accumulated_text_len
            events: list[tuple[str, object | None]] = []
            usage = parse_chunk_usage(parsed_chunk)
            if usage:
                latest_usage = usage
            if (
                isinstance(parsed_chunk, list)
                and len(parsed_chunk) > WIRE_RESPONSE_ID_INDEX
                and isinstance(parsed_chunk[WIRE_RESPONSE_ID_INDEX], str)
                and parsed_chunk[WIRE_RESPONSE_ID_INDEX]
            ):
                latest_response_id = str(parsed_chunk[WIRE_RESPONSE_ID_INDEX])
                events.append(("response_id", latest_response_id))
            candidate = parse_response_chunk(parsed_chunk)
            if candidate.finish_reason is not None and candidate.finish_reason not in (
                0,
                WIRE_FINISH_REASON_CONTINUING,
            ):
                latest_finish_reason = candidate.finish_reason

            def _flush_batch(kind: str, items: list[object]) -> None:
                if not items:
                    return
                event_name = {
                    "function_call": "tool_calls",
                    "image": "images",
                    "reasoning_image": "reasoning_images",
                }[kind]
                events.append((event_name, list(items)))
                items.clear()

            batch_kind = ""
            batch_items: list[object] = []
            for part in candidate.parts:
                if part.kind in ("function_call", "image", "reasoning_image"):
                    item: object | None = (
                        part.payload if part.kind == "function_call" else part.image
                    )
                    if item is None:
                        continue
                    if batch_kind and batch_kind != part.kind:
                        _flush_batch(batch_kind, batch_items)
                    batch_kind = part.kind
                    batch_items.append(item)
                    continue
                _flush_batch(batch_kind, batch_items)
                batch_kind = ""
                if part.kind == "thinking":
                    if part.thought_signature:
                        events.append(
                            (
                                "thought_signature",
                                ThoughtSignatureEvent(
                                    part.thought_signature,
                                    standalone=False,
                                    thought=True,
                                    end_of_chunk=part.end_of_chunk,
                                ),
                            )
                        )
                    if part.text:
                        events.append(("thinking", part.text))
                elif part.kind == "text":
                    if part.text and accumulated_text_len < 512:
                        accumulated_text_parts.append(part.text)
                        accumulated_text_len += len(part.text)
                    if is_upstream_policy_block_text("".join(accumulated_text_parts)):
                        raise RequestError(
                            400, "Upstream prompt blocked by Google policy"
                        )
                    if part.thought_signature:
                        events.append(
                            (
                                "thought_signature",
                                ThoughtSignatureEvent(
                                    part.thought_signature,
                                    standalone=False,
                                    thought=False,
                                    end_of_chunk=part.end_of_chunk,
                                ),
                            )
                        )
                    if part.text:
                        events.append(("body", part.text))
                elif part.kind == "standalone_signature":
                    if part.thought_signature:
                        events.append(
                            (
                                "thought_signature",
                                ThoughtSignatureEvent(
                                    part.thought_signature,
                                    standalone=True,
                                    thought=part.thought,
                                    empty_text_carrier=part.has_text,
                                    end_of_chunk=part.end_of_chunk,
                                ),
                            )
                        )
                elif part.kind in (
                    "executable_code",
                    "code_execution_result",
                    "file_data",
                    "video_metadata",
                    "function_response",
                ):
                    events.append(("candidate_part", part))
            _flush_batch(batch_kind, batch_items)
            return events

        async with aclosing(
            self._session.send_streaming_request(
                body=request_body,
                url=request.url,
                headers=request.headers,
                ctx=request.ctx,
            )
        ) as events:
            async for event_type, payload in events:
                if event_type == "error":
                    err_msg = (
                        str(payload).strip()
                        if payload
                        else "unknown streaming transport error"
                    )
                    raise TransportError(f"Streaming request failed: {err_msg}")
                if event_type == "status" and payload and not status_code:
                    status_code = int(str(payload))
                elif event_type == "chunk" and payload:
                    has_received_chunks = True
                    if isinstance(payload, bytes):
                        text_payload = utf8_decoder.decode(payload, final=False)
                    else:
                        text_payload = str(payload)
                    if not text_payload:
                        continue
                    if status_code != 200:
                        raw_parts.append(text_payload)
                    elif len(head_sample) < 512:
                        head_sample += text_payload[: 512 - len(head_sample)]
                    for parsed_chunk in parser.feed(text_payload):
                        for event in emit_parsed_chunk(parsed_chunk):
                            emitted_any_chunk = True
                            yield event
        trailing_text = utf8_decoder.decode(b"", final=True)
        if trailing_text:
            if status_code != 200:
                raw_parts.append(trailing_text)
            for parsed_chunk in parser.feed(trailing_text):
                for event in emit_parsed_chunk(parsed_chunk):
                    emitted_any_chunk = True
                    yield event

        for parsed_chunk in parser.finish():
            for event in emit_parsed_chunk(parsed_chunk):
                emitted_any_chunk = True
                yield event
        logger.debug("流式读取结束: model=%s, 收到分块=%s", model, has_received_chunks)
        raw_response = "".join(raw_parts)
        if status_code == 0:
            raise TransportError(
                "stream ended before HTTP response status arrived (connection failed)"
            )
        if status_code != 200:
            detail = _summarize_error_body(raw_response)
            if (
                status_code in (401, 403, 429)
                or "[,[" in raw_response
                or "[null,[" in raw_response
                or raw_response.lstrip().startswith("[,")
            ):
                raise classify_error(status_code, raw_response)
            if detail:
                raise RequestError(status_code, detail)
            raise RequestError(status_code, "")
        if not has_received_chunks:
            raise TransportError(
                "stream ended before any data arrived (connection truncated)"
            )
        if not emitted_any_chunk and head_sample:
            if is_login_redirect_payload(head_sample):
                raise SessionExpiredError("登录态已失效: 被重定向至 Google 登录页")
            if is_html_payload(head_sample):
                raise TransportError("upstream returned HTML error page on HTTP 200")
        json_truncated = (
            parser.depth > 0 or parser.chunk_start is not None or parser.in_string
        )
        if is_terminal_wire_finish_reason(latest_finish_reason):
            yield ("finish_reason", latest_finish_reason)
        elif json_truncated or is_gemini4_model(model):
            raise TransportError(
                "stream ended without a terminal finish_reason (incomplete response)"
            )
        else:
            yield ("finish_reason", 1)
        yield ("usage", latest_usage)
        yield ("done", None)
