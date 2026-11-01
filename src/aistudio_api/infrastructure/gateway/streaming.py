"""Streaming workflow for chat completions."""

from __future__ import annotations

import codecs
import json
from collections.abc import AsyncGenerator

from aistudio_api.domain.errors import (
    RequestError,
    TransportError,
    classify_error,
)
from aistudio_api.infrastructure.gateway.client import PreparedRequest
from aistudio_api.infrastructure.gateway.session import GatewaySession
from aistudio_api.infrastructure.gateway.stream_parser import (
    IncrementalJSONStreamParser,
)
from aistudio_api.infrastructure.gateway.wire_parser import (
    WIRE_RESPONSE_ID_INDEX,
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
        logger.debug("开始流式处理: model=%s, 请求体长度=%d", model, len(request_body))
        parser = IncrementalJSONStreamParser()
        utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        latest_usage: dict[str, object] | None = None
        latest_finish_reason: int | None = None
        latest_response_id: str | None = None
        raw_parts: list[str] = []
        has_received_chunks = False
        status_code = 0

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
                events.append(("thinking", candidate.thinking))
            if candidate.reasoning_images:
                events.append(("reasoning_images", candidate.reasoning_images))
            if candidate.function_calls:
                events.append(("tool_calls", candidate.function_calls))
            if candidate.images:
                events.append(("images", candidate.images))
            if candidate.text:
                events.append(("body", candidate.text))
            if candidate.thought_signature:
                events.append(("thought_signature", candidate.thought_signature))
            return events

        async for event_type, payload in self._session.send_streaming_request(
            body=request_body,
            url=request.url,
            headers=request.headers,
            ctx=request.ctx,
        ):
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
                for parsed_chunk in parser.feed(text_payload):
                    for event in emit_parsed_chunk(parsed_chunk):
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
                yield event
        logger.debug(
            "流式数据流接收结束: model=%s, 分块到达=%s", model, has_received_chunks
        )
        raw_response = "".join(raw_parts)
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
        if latest_finish_reason is not None:
            yield ("finish_reason", latest_finish_reason)
        else:
            yield ("finish_reason", 1)
        yield ("usage", latest_usage)
        yield ("done", None)
