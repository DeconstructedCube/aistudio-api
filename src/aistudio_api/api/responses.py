"""Gemini-compatible HTTP/SSE response helpers."""

from __future__ import annotations

import base64

from aistudio_api.api.response_models import (
    GeminiFunctionCallPayload,
    GeminiFunctionResponsePayload,
    GeminiInlineDataResponse,
    GeminiPartResponse,
    GeminiUsageMetadata,
)
from aistudio_api.domain.models import GeneratedImage


def _coerce_usage_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and (
            stripped.isdigit() or (stripped[0] in "+-" and stripped[1:].isdigit())
        ):
            return int(stripped)
    return 0


def to_gemini_usage_metadata(
    usage: dict[str, object] | None = None,
) -> GeminiUsageMetadata:
    usage = usage or {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    reasoning_tokens = _coerce_usage_int(completion_details.get("reasoning_tokens"))
    visible_tokens = _coerce_usage_int(completion_details.get("visible_tokens"))
    completion_tokens = _coerce_usage_int(usage.get("completion_tokens"))
    if completion_tokens == 0:
        completion_tokens = visible_tokens + reasoning_tokens
    candidates_tokens = (
        visible_tokens if completion_details else completion_tokens
    )
    # Gemini 语义：candidatesTokenCount 只统计可见输出，思考 token 单独计入
    # thoughtsTokenCount，且 totalTokenCount = prompt + candidates + thoughts。
    # 上游的 completion_tokens 已包含 reasoning，直接赋给 candidates 会让
    # candidates + thoughts 超过 total（思考 token 被重复计数）。
    prompt_tokens = _coerce_usage_int(usage.get("prompt_tokens"))
    total_tokens = _coerce_usage_int(usage.get("total_tokens"))
    if total_tokens == 0 and (
        prompt_tokens or candidates_tokens or reasoning_tokens
    ):
        total_tokens = prompt_tokens + candidates_tokens + reasoning_tokens
    raw_cached = usage.get("cached_tokens")
    cached_tokens = (
        _coerce_usage_int(raw_cached)
        if raw_cached is not None and not isinstance(raw_cached, bool)
        else None
    )
    return GeminiUsageMetadata(
        promptTokenCount=prompt_tokens,
        candidatesTokenCount=candidates_tokens,
        thoughtsTokenCount=reasoning_tokens,
        totalTokenCount=total_tokens,
        cachedContentTokenCount=cached_tokens,
    )


def to_gemini_parts(
    content: str,
    function_calls: list[dict[str, object]] | None = None,
    function_responses: list[dict[str, object]] | None = None,
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
                ),
            )
        )
    for function_call in function_calls or []:
        fc_name = str(function_call.get("name") or "unknown")
        payload = GeminiFunctionCallPayload(name=fc_name)
        if "args" in function_call:
            payload.args = function_call["args"]
        elif "arguments" in function_call:
            payload.args = function_call["arguments"]
        else:
            raw_val = function_call.get("raw")
            if isinstance(raw_val, list) and len(raw_val) > 1:
                payload.args = raw_val[1]
        if payload.args is None:
            payload.args = {}
        fc_id = function_call.get("call_id") or function_call.get("id")
        if fc_id:
            payload.id = str(fc_id)
        sig = str(function_call.get("thought_signature") or "") or None
        parts.append(GeminiPartResponse(functionCall=payload, thoughtSignature=sig))
    for function_response in function_responses or []:
        fr_name = str(function_response.get("name") or "unknown")
        resp_payload = GeminiFunctionResponsePayload(name=fr_name)
        if "args" in function_response:
            resp_payload.response = function_response["args"]
        elif "arguments" in function_response:
            resp_payload.response = function_response["arguments"]
        else:
            raw_val = function_response.get("raw")
            if isinstance(raw_val, list) and len(raw_val) > 1:
                resp_payload.response = raw_val[1]
        fr_id = function_response.get("call_id") or function_response.get("id")
        if fr_id:
            resp_payload.id = str(fr_id)
        parts.append(GeminiPartResponse(functionResponse=resp_payload))
    if not parts:
        parts.append(GeminiPartResponse(text=""))
    return parts
