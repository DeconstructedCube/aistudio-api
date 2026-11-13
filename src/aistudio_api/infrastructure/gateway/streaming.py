"""Streaming workflow for chat completions."""

from __future__ import annotations

import codecs
import json
from collections.abc import AsyncGenerator

from aistudio_api.domain.errors import (
    RequestError,
    SessionExpiredError,
    TransportError,
    classify_error,
    is_html_payload,
    is_login_redirect_payload,
)
from aistudio_api.infrastructure.gateway.client import PreparedRequest
from aistudio_api.infrastructure.gateway.session import GatewaySession
from aistudio_api.infrastructure.gateway.stream_parser import (
    IncrementalJSONStreamParser,
)
from aistudio_api.infrastructure.gateway.wire_parser import (
    WIRE_RESPONSE_ID_INDEX,
    may_start_inline_thought,
    parse_chunk_usage,
    parse_response_chunk,
    split_closed_inline_thought,
    split_inline_thought_tag,
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
        body_started = False
        inline_thought_buf = ""
        pending_text_sig: str | None = None

        def _flush_inline_thought_prefix(
            chunk_text: str, text_sig: str = ""
        ) -> list[tuple[str, object | None]]:
            nonlocal body_started, inline_thought_buf, pending_text_sig
            out: list[tuple[str, object | None]] = []
            if body_started:
                if text_sig:
                    out.append(("thought_signature", text_sig))
                out.append(("body", chunk_text))
                return out

            if text_sig:
                pending_text_sig = text_sig
            inline_thought_buf += chunk_text
            closed = split_closed_inline_thought(inline_thought_buf)
            if closed is not None:
                thought_body, remainder = closed
                inline_thought_buf = ""
                body_started = True
                if thought_body:
                    out.append(("thinking", thought_body))
                if pending_text_sig:
                    out.append(("thought_signature", pending_text_sig))
                    pending_text_sig = None
                if remainder:
                    out.append(("body", remainder))
                return out

            if may_start_inline_thought(inline_thought_buf):
                return out

            flushed = inline_thought_buf
            inline_thought_buf = ""
            body_started = True
            if pending_text_sig:
                out.append(("thought_signature", pending_text_sig))
                pending_text_sig = None
            if flushed:
                out.append(("body", flushed))
            return out

        def emit_parsed_chunk(
            parsed_chunk: list[object],
        ) -> list[tuple[str, object | None]]:
            nonlocal latest_usage, latest_finish_reason, latest_response_id
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
            if candidate.thinking:
                if candidate.thinking_signature:
                    events.append(("thought_signature", candidate.thinking_signature))
                events.append(("thinking", candidate.thinking))
            if candidate.reasoning_images:
                events.append(("reasoning_images", candidate.reasoning_images))
            if candidate.function_calls:
                events.append(("tool_calls", candidate.function_calls))
            if candidate.images:
                events.append(("images", candidate.images))
            if candidate.text:
                events.extend(
                    _flush_inline_thought_prefix(
                        candidate.text, text_sig=candidate.text_signature
                    )
                )
            elif candidate.text_signature:
                events.append(("thought_signature", candidate.text_signature))
            if candidate.standalone_signature:
                events.append(("thought_signature", candidate.standalone_signature))
            return events

        async for event_type, payload in self._session.send_streaming_request(
            body=request_body,
            url=request.url,
            headers=request.headers,
            ctx=request.ctx,
        ):
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
                    yield event

        for parsed_chunk in parser.finish():
            for event in emit_parsed_chunk(parsed_chunk):
                emitted_any_chunk = True
                yield event
        if inline_thought_buf or pending_text_sig:
            thought_body, remainder = split_inline_thought_tag(inline_thought_buf)
            inline_thought_buf = ""
            body_started = True
            if thought_body:
                emitted_any_chunk = True
                yield ("thinking", thought_body)
            if pending_text_sig:
                emitted_any_chunk = True
                yield ("thought_signature", pending_text_sig)
                pending_text_sig = None
            if remainder:
                emitted_any_chunk = True
                yield ("body", remainder)
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
        if latest_finish_reason is not None:
            yield ("finish_reason", latest_finish_reason)
        else:
            yield ("finish_reason", 1)
        yield ("usage", latest_usage)
        yield ("done", None)
