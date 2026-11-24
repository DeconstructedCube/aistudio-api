"""Gemini-compatible HTTP/SSE response helpers."""

from __future__ import annotations

import base64
from collections.abc import Sequence

from aistudio_api.api.response_models import (
    GeminiCodeExecutionResultResponse,
    GeminiExecutableCodeResponse,
    GeminiFileDataResponse,
    GeminiFunctionCallPayload,
    GeminiFunctionResponsePayload,
    GeminiInlineDataResponse,
    GeminiPartResponse,
    GeminiUsageMetadata,
    GeminiVideoMetadataResponse,
)
from aistudio_api.domain.models import CandidatePart


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
    candidates_tokens = completion_tokens
    prompt_tokens = _coerce_usage_int(usage.get("prompt_tokens"))
    total_tokens = _coerce_usage_int(usage.get("total_tokens"))
    if total_tokens == 0 and (prompt_tokens or candidates_tokens):
        total_tokens = prompt_tokens + candidates_tokens
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


def _video_metadata_from_payload(
    payload: dict[str, object] | None,
) -> GeminiVideoMetadataResponse | None:
    if not payload:
        return None
    raw_vm = payload.get("video_metadata") if "video_metadata" in payload else payload
    if not isinstance(raw_vm, dict):
        return None
    start_offset = raw_vm.get("startOffset") or raw_vm.get("start_offset")
    end_offset = raw_vm.get("endOffset") or raw_vm.get("end_offset")
    raw_fps = raw_vm.get("fps")
    fps = (
        float(raw_fps)
        if isinstance(raw_fps, (int, float)) and not isinstance(raw_fps, bool)
        else None
    )
    if start_offset is None and end_offset is None and fps is None:
        return None
    return GeminiVideoMetadataResponse(
        startOffset=str(start_offset) if start_offset is not None else None,
        endOffset=str(end_offset) if end_offset is not None else None,
        fps=fps,
    )


def to_gemini_parts(
    candidate_parts: Sequence[CandidatePart],
    *,
    allow_empty_fallback: bool = True,
) -> list[GeminiPartResponse]:
    parts: list[GeminiPartResponse] = []
    for part in candidate_parts:
        sig = part.thought_signature or None
        if part.kind == "thinking":
            if part.text or sig:
                parts.append(
                    GeminiPartResponse(
                        text=part.text,
                        thought=True,
                        thoughtSignature=sig,
                    )
                )
        elif part.kind == "text":
            if part.text or sig:
                parts.append(
                    GeminiPartResponse(
                        text=part.text,
                        thoughtSignature=sig,
                    )
                )
        elif part.kind in ("image", "reasoning_image"):
            if part.image is None:
                continue
            parts.append(
                GeminiPartResponse(
                    thought=True if part.kind == "reasoning_image" else None,
                    thoughtSignature=sig or part.image.thought_signature or None,
                    inlineData=GeminiInlineDataResponse(
                        mimeType=part.image.mime,
                        data=base64.b64encode(part.image.data).decode("ascii"),
                    ),
                    videoMetadata=_video_metadata_from_payload(part.payload),
                )
            )
        elif part.kind == "function_call":
            fc = part.payload or {}
            fc_name = str(fc.get("name") or "unknown")
            payload = GeminiFunctionCallPayload(name=fc_name)
            if "args" in fc:
                payload.args = fc["args"]
            elif "arguments" in fc:
                payload.args = fc["arguments"]
            else:
                raw_val = fc.get("raw")
                if isinstance(raw_val, list) and len(raw_val) > 1:
                    payload.args = raw_val[1]
            if payload.args is None:
                payload.args = {}
            fc_id = fc.get("call_id") or fc.get("id")
            if fc_id:
                payload.id = str(fc_id)
            effective_sig = sig or str(fc.get("thought_signature") or "") or None
            parts.append(
                GeminiPartResponse(functionCall=payload, thoughtSignature=effective_sig)
            )
        elif part.kind == "function_response":
            fr = part.payload or {}
            fr_name = str(fr.get("name") or "unknown")
            resp_payload = GeminiFunctionResponsePayload(name=fr_name)
            if "args" in fr:
                resp_payload.response = fr["args"]
            elif "arguments" in fr:
                resp_payload.response = fr["arguments"]
            else:
                raw_val = fr.get("raw")
                if isinstance(raw_val, list) and len(raw_val) > 1:
                    resp_payload.response = raw_val[1]
            fr_id = fr.get("call_id") or fr.get("id")
            if fr_id:
                resp_payload.id = str(fr_id)
            parts.append(
                GeminiPartResponse(functionResponse=resp_payload, thoughtSignature=sig)
            )
        elif part.kind == "executable_code":
            ec = part.payload or {}
            parts.append(
                GeminiPartResponse(
                    executableCode=GeminiExecutableCodeResponse(
                        language=str(ec.get("language") or "PYTHON"),
                        code=str(ec.get("code") or ""),
                        id=str(ec["id"]) if ec.get("id") else None,
                    ),
                    thought=True if part.thought else None,
                    thoughtSignature=sig,
                )
            )
        elif part.kind == "code_execution_result":
            cer = part.payload or {}
            parts.append(
                GeminiPartResponse(
                    codeExecutionResult=GeminiCodeExecutionResultResponse(
                        outcome=str(cer.get("outcome") or "OUTCOME_OK"),
                        output=str(cer.get("output") or ""),
                        id=str(cer["id"]) if cer.get("id") else None,
                    ),
                    thought=True if part.thought else None,
                    thoughtSignature=sig,
                )
            )
        elif part.kind == "file_data":
            fd = part.payload or {}
            file_uri = str(fd.get("fileUri") or fd.get("file_uri") or "")
            if file_uri:
                raw_mime = fd.get("mimeType") or fd.get("mime_type")
                parts.append(
                    GeminiPartResponse(
                        fileData=GeminiFileDataResponse(
                            mimeType=str(raw_mime) if raw_mime else None,
                            fileUri=file_uri,
                        ),
                        thought=True if part.thought else None,
                        thoughtSignature=sig,
                    )
                )
        elif part.kind == "video_metadata":
            vm = _video_metadata_from_payload(part.payload)
            if vm is not None:
                parts.append(
                    GeminiPartResponse(
                        videoMetadata=vm,
                        thought=True if part.thought else None,
                        thoughtSignature=sig,
                    )
                )
        elif part.kind == "standalone_signature" and sig:
            parts.append(
                GeminiPartResponse(
                    text="" if part.has_text else None,
                    thought=True if part.thought else None,
                    thoughtSignature=sig,
                )
            )
    if not parts and allow_empty_fallback:
        parts.append(GeminiPartResponse(text=""))
    return parts
