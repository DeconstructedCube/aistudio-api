"""Gemini-compatible HTTP/SSE response helpers."""

from __future__ import annotations

import base64
from typing import Any

from aistudio_api.api.response_models import (
    ErrorDetail,
    ErrorResponse,
    GeminiFunctionCallPayload,
    GeminiFunctionResponsePayload,
    GeminiInlineDataResponse,
    GeminiPartResponse,
    GeminiUsageMetadata,
)
from aistudio_api.domain.models import GeneratedImage


def _coerce_usage_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and (stripped.isdigit() or (stripped[0] in "+-" and stripped[1:].isdigit())):
            return int(stripped)
    return 0


def to_gemini_usage_metadata(usage: dict | None = None) -> GeminiUsageMetadata:
    usage = usage or {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    reasoning_tokens = _coerce_usage_int(completion_details.get("reasoning_tokens"))
    visible_tokens = _coerce_usage_int(completion_details.get("visible_tokens"))
    candidates_tokens = visible_tokens or _coerce_usage_int(usage.get("completion_tokens"))
    prompt_tokens = _coerce_usage_int(usage.get("prompt_tokens"))
    total_tokens = _coerce_usage_int(usage.get("total_tokens"))
    if total_tokens == 0 and (prompt_tokens or candidates_tokens or reasoning_tokens):
        total_tokens = prompt_tokens + _coerce_usage_int(usage.get("completion_tokens"))
    return GeminiUsageMetadata(
        promptTokenCount=prompt_tokens,
        candidatesTokenCount=candidates_tokens,
        thoughtsTokenCount=reasoning_tokens,
        totalTokenCount=total_tokens,
    )


def sse_error(message: str) -> str:
    data = ErrorResponse(error=ErrorDetail(message=message, type="server_error"))
    return f"data: {data.model_dump_json()}\n\n"
def to_gemini_parts(
    content: str,
    function_calls: list[dict[str, Any]] | None = None,
    function_responses: list[dict[str, Any]] | None = None,
    thinking: str = "",
    images: list[GeneratedImage] | None = None,
    reasoning_images: list[GeneratedImage] | None = None,
) -> list[GeminiPartResponse]:
    parts: list[GeminiPartResponse] = []
    if thinking:
        parts.append(GeminiPartResponse(text=thinking, thought=True))
    for image in reasoning_images or []:
        parts.append(
            GeminiPartResponse(
                thought=True,
                thoughtSignature=image.thought_signature or None,
                inlineData=GeminiInlineDataResponse(
                    mimeType=image.mime,
                    data=base64.b64encode(image.data).decode("ascii"),
                ),
            )
        )
    if content:
        parts.append(GeminiPartResponse(text=content))
    for image in images or []:
        parts.append(
            GeminiPartResponse(
                thoughtSignature=image.thought_signature or None,
                inlineData=GeminiInlineDataResponse(
                    mimeType=image.mime,
                    data=base64.b64encode(image.data).decode("ascii"),
                )
            )
        )
    for function_call in function_calls or []:
        payload = GeminiFunctionCallPayload(name=function_call.get("name", "unknown"))
        if "args" in function_call:
            payload.args = function_call["args"]
        elif "arguments" in function_call:
            payload.args = function_call["arguments"]
        elif isinstance(function_call.get("raw"), list) and len(function_call["raw"]) > 1:
            payload.args = function_call["raw"][1]
        parts.append(GeminiPartResponse(functionCall=payload))
    for function_response in function_responses or []:
        payload = GeminiFunctionResponsePayload(name=function_response.get("name", "unknown"))
        if "args" in function_response:
            payload.response = function_response["args"]
        elif "arguments" in function_response:
            payload.response = function_response["arguments"]
        elif isinstance(function_response.get("raw"), list) and len(function_response["raw"]) > 1:
            payload.response = function_response["raw"][1]
        parts.append(GeminiPartResponse(functionResponse=payload))
    if not parts:
        parts.append(GeminiPartResponse(text=""))
    return parts

