"""Gemini-compatible application service handlers."""

from __future__ import annotations

import json
import re

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import (
    GeminiCandidateResponse,
    GeminiContentResponse,
    GeminiGenerateContentResponse,
)
from aistudio_api.api.responses import to_gemini_parts, to_gemini_usage_metadata
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    MAX_RETRIES,
    ensure_active_account,
    record_rotator_event,
    try_switch_account,
)
from aistudio_api.application.chat_service import (
    cleanup_files,
    normalize_gemini_request,
)
from aistudio_api.domain.errors import (
    AistudioError,
    AuthError,
    RequestError,
    SessionExpiredError,
    UsageLimitExceeded,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("api.gemini")


WIRE_TO_GEMINI_FINISH_REASON: dict[int, str] = {
    0: "FINISH_REASON_UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "LANGUAGE",
    6: "OTHER",
    7: "BLOCKLIST",
    8: "PROHIBITED_CONTENT",
    9: "SPII",
    10: "MALFORMED_FUNCTION_CALL",
    11: "IMAGE_SAFETY",
    12: "UNEXPECTED_TOOL_CALL",
    13: "TOO_MANY_TOOL_CALLS",
    14: "IMAGE_PROHIBITED_CONTENT",
    15: "NO_IMAGE",
    16: "IMAGE_RECITATION",
    17: "IMAGE_OTHER",
}


def to_gemini_finish_reason(wire_code: int | None) -> str:
    if wire_code is None:
        return "STOP"
    return WIRE_TO_GEMINI_FINISH_REASON.get(wire_code, "STOP")


def clean_upstream_error_message(raw_msg: str) -> str:
    """Extract a clean, readable error message from Google/JSPB upstream error strings."""
    if not raw_msg:
        return ""
    text = raw_msg.strip()

    # Only unpack if it contains a JSPB bracket array like [,[ or [null,[
    if "[,[" not in text and "[null,[" not in text and not text.startswith("[,"):
        return text

    m = re.match(r"^HTTP\s+\d+:\s*(.*)$", text)
    inner = m.group(1).strip() if m else text

    normalized = inner
    if normalized.startswith("[,"):
        normalized = "[null," + normalized[2:]

    try:
        data = json.loads(normalized)

        def find_msg(obj):
            if isinstance(obj, list):
                if len(obj) >= 2 and isinstance(obj[1], str) and obj[1]:
                    return obj[1]
                for item in obj:
                    res = find_msg(item)
                    if res:
                        return res
            return None

        extracted = find_msg(data)
        if extracted:
            return extracted
    except Exception:
        pass

    match = re.search(r'\[\s*,\s*\[\s*\d+\s*,\s*"([^"\\]*(?:\\.[^"\\]*)*)"', inner)
    if match:
        try:
            return json.loads(f'"{match.group(1)}"')
        except Exception:
            return match.group(1)

    match2 = re.search(
        r'\[\s*null\s*,\s*\[\s*\d+\s*,\s*"([^"\\]*(?:\\.[^"\\]*)*)"', inner
    )
    if match2:
        try:
            return json.loads(f'"{match2.group(1)}"')
        except Exception:
            return match2.group(1)

    return text


def classify_gemini_error_payload(exc: Exception) -> tuple[int, str, str]:
    """Extract standard Gemini HTTP status code, message, and status string from an exception."""
    if isinstance(exc, SessionExpiredError):
        return (
            401,
            "All accounts have expired sessions. Please import fresh cookies.",
            "UNAUTHENTICATED",
        )
    if isinstance(exc, AuthError):
        return 403, str(exc), "PERMISSION_DENIED"
    if isinstance(exc, UsageLimitExceeded):
        return 429, str(exc), "RESOURCE_EXHAUSTED"
    if isinstance(exc, (ValueError, RequestValidationError)):
        return 400, str(exc), "INVALID_ARGUMENT"
    if isinstance(exc, HTTPException):
        status = exc.status_code
        detail = exc.detail
        raw_msg = detail.get("message") if isinstance(detail, dict) else detail
        msg = str(raw_msg) if raw_msg is not None else ""
        clean_msg = clean_upstream_error_message(msg)
        status_map = {
            400: "INVALID_ARGUMENT",
            401: "UNAUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            429: "RESOURCE_EXHAUSTED",
            500: "INTERNAL",
            503: "UNAVAILABLE",
        }
        return (
            status,
            clean_msg,
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )
    if isinstance(exc, RequestError):
        status = exc.status if exc.status > 0 else 500
        clean_msg = clean_upstream_error_message(str(exc))
        status_map = {
            400: "INVALID_ARGUMENT",
            401: "UNAUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            429: "RESOURCE_EXHAUSTED",
            500: "INTERNAL",
            503: "UNAVAILABLE",
        }
        return (
            status,
            clean_msg,
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )
    if isinstance(exc, AistudioError):
        return 500, str(exc), "INTERNAL"

    return 500, str(exc), "INTERNAL"


async def handle_attempt_exception(
    exc: Exception,
    *,
    attempt: int,
    model_path: str,
    normalized_model: str | None,
    client: AIStudioClient,
    has_yielded_data: bool = False,
) -> bool:
    """Handle per-attempt error classification and account switching.

    Returns True if the operation should be retried, or raises HTTPException / re-raises.
    """
    target_model = normalized_model or model_path
    account_svc = runtime_state.account_service
    active_acc = account_svc.get_active_account() if account_svc else None
    failed_id = active_acc.id if active_acc else None

    if isinstance(exc, ValueError):
        raise HTTPException(
            400, detail={"message": str(exc), "type": "bad_request"}
        ) from exc

    if isinstance(exc, SessionExpiredError):
        logger.warning("账号会话重定向至登录页: %s", exc)
        client.clear_templates()
        if not has_yielded_data and await try_switch_account(
            model=target_model, failed_account_id=failed_id, is_auth_error=False
        ):
            logger.info("切换至可用账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        raise HTTPException(
            401,
            detail={
                "message": "All accounts encountered login redirect or expired sessions. Please import fresh cookies.",
                "type": "auth_error",
            },
        ) from exc

    if isinstance(exc, AuthError):
        logger.warning("账号 403 权限拒绝: %s", exc)
        client.clear_templates()
        record_rotator_event("auth_error", model=target_model)
        if not has_yielded_data and await try_switch_account(
            model=target_model, failed_account_id=failed_id, is_auth_error=True
        ):
            logger.info("切换至可用账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        raise HTTPException(
            403, detail={"message": str(exc), "type": "auth_error"}
        ) from exc

    if isinstance(exc, UsageLimitExceeded):
        runtime_state.record(target_model, "rate_limited")
        record_rotator_event("rate_limited", model=target_model)
        if not has_yielded_data and await try_switch_account(
            model=target_model, failed_account_id=failed_id
        ):
            logger.info(
                "429 触发限额，切换账号 (%d/%d): model=%s",
                attempt + 1,
                MAX_RETRIES,
                target_model,
            )
            return True
        logger.warning("全部账号在模型 %s 上均处于冷却状态", target_model)
        raise HTTPException(
            429,
            detail={
                "message": f"All accounts reached daily quota for model '{target_model}'. Quota resets at 00:00 PST.",
                "type": "rate_limit_exceeded",
            },
        ) from exc

    if (
        isinstance(exc, RequestError)
        and exc.status == 204
        and attempt == 0
        and not has_yielded_data
    ):
        logger.warning("Gemini 收到 204，清理模板缓存后重试一次")
        client.clear_templates()
        return True

    if isinstance(exc, (RuntimeError, TimeoutError)):
        err_msg = str(exc).lower()
        if (
            "template capture" in err_msg
            or "botguard" in err_msg
            or "timeout" in err_msg
            or "cdp" in err_msg
            or "closed" in err_msg
            or "aborted" in err_msg
        ):
            logger.warning(
                "模板或 BotGuard 捕获超时 (%d/%d): %s，切换账号重试",
                attempt + 1,
                MAX_RETRIES,
                exc,
            )
            client.clear_templates()
            if not has_yielded_data and await try_switch_account(
                model=target_model, failed_account_id=failed_id, is_auth_error=False
            ):
                logger.info("已切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
                return True
            if attempt < 2 and not has_yielded_data:
                if client._session is not None:
                    await client._session._close_internal()
                return True

    if isinstance(exc, RequestError):
        clean_msg = clean_upstream_error_message(str(exc))
        status_code = exc.status if exc.status > 0 else 500
        error_type = (
            "bad_request"
            if status_code == 400
            else "not_found"
            if status_code == 404
            else "upstream_error"
        )
        logger.warning("Gemini 上游请求错误 (%d): %s", status_code, clean_msg)
        raise HTTPException(
            status_code, detail={"message": clean_msg, "type": error_type}
        ) from exc

    if isinstance(exc, AistudioError):
        runtime_state.record(target_model, "errors")
        record_rotator_event("error", model=target_model)
        logger.warning("Gemini 服务端异常: %s", exc)
        raise HTTPException(
            500, detail={"message": str(exc), "type": "server_error"}
        ) from exc

    runtime_state.record(target_model, "errors")
    record_rotator_event("error", model=target_model)
    logger.error("Gemini 未知严重异常: %s", exc)
    logger.debug("Gemini 异常堆栈详情:", exc_info=True)
    raise HTTPException(
        500, detail={"message": str(exc), "type": "server_error"}
    ) from exc


async def handle_gemini_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    *,
    stream: bool,
):
    if stream:
        return _build_gemini_streaming_response(
            client=client, req=req, model_path=model_path
        )

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        await ensure_active_account(attempt, model=model_path)
        normalized = None
        try:
            normalized = normalize_gemini_request(req, model_path)
            logger.info(
                "Gemini 生成请求: model=%s, 对话轮数=%s, 流式=否, 尝试第 %d 次",
                normalized.model,
                len(req.contents),
                attempt + 1,
            )
            output = await client.generate_content(
                model=normalized.model,
                capture_prompt=normalized.capture_prompt,
                capture_images=normalized.capture_images,
                contents=normalized.contents,
                system_instruction_content=normalized.system_instruction,
                tools=normalized.tools,
                tool_config=normalized.tool_config,
                safety_settings=normalized.safety_settings,
                temperature=normalized.temperature,
                top_p=normalized.top_p,
                top_k=normalized.top_k,
                max_tokens=normalized.max_tokens,
                generation_config_overrides=normalized.generation_config_overrides,
                sanitize_plain_text=False,
            )

            record_rotator_event("success", model=normalized.model)
            runtime_state.record(normalized.model, "success", output.usage)
            logger.info(
                "Gemini 生成完成: model=%s, 输入Tokens=%s, 输出Tokens=%s",
                normalized.model,
                output.usage.get("prompt_tokens") if output.usage else 0,
                output.usage.get("completion_tokens") if output.usage else 0,
            )
            return GeminiGenerateContentResponse(
                candidates=[
                    GeminiCandidateResponse(
                        content=GeminiContentResponse(
                            parts=to_gemini_parts(
                                output.text,
                                function_calls=output.function_calls,
                                function_responses=output.function_responses,
                                thinking=output.thinking,
                                images=output.images,
                                reasoning_images=output.reasoning_images,
                            ),
                        ),
                        finishReason=to_gemini_finish_reason(
                            output.candidates[0].finish_reason
                            if output.candidates
                            else None
                        ),
                        index=0,
                    )
                ],
                usageMetadata=to_gemini_usage_metadata(output.usage),
                modelVersion=normalized.model,
                responseId=output.response_id or None,
            )
        except Exception as exc:
            last_error = exc
            if await handle_attempt_exception(
                exc,
                attempt=attempt,
                model_path=model_path,
                normalized_model=normalized.model if normalized else None,
                client=client,
            ):
                continue
            raise
        finally:
            if normalized is not None and not stream:
                cleanup_files(normalized.cleanup_paths)

    raise HTTPException(
        429,
        detail={
            "message": f"All accounts reached daily quota for model '{model_path}'. Quota resets at 00:00 PST.",
            "type": "rate_limit_exceeded",
        },
    ) from last_error


def format_sse_event(
    event_type: str,
    text: object,
    *,
    model_version: str | None = None,
    response_id: str | None = None,
    thought_signature: str | None = None,
) -> str | None:
    """Format a single Gemini streaming event into SSE chunk string."""
    if not text:
        return None

    parts: list[dict[str, object]] = []
    if event_type == "body":
        p: dict[str, object] = {"text": str(text)}
        if thought_signature:
            p["thoughtSignature"] = thought_signature
        parts.append(p)
    elif event_type == "thinking":
        p = {"text": str(text), "thought": True}
        if thought_signature:
            p["thoughtSignature"] = thought_signature
        parts.append(p)
    elif event_type == "tool_calls":
        fc_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", function_calls=fc_list)
        ]
        if thought_signature and parts:
            parts[0]["thoughtSignature"] = thought_signature
    elif event_type == "images":
        img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", images=img_list)
        ]
        if thought_signature and parts:
            parts[0]["thoughtSignature"] = thought_signature
    elif event_type == "reasoning_images":
        r_img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", reasoning_images=r_img_list)
        ]
        if thought_signature and parts:
            parts[0]["thoughtSignature"] = thought_signature
    else:
        return None

    payload: dict[str, object] = {
        "candidates": [{"content": {"role": "model", "parts": parts}, "index": 0}]
    }
    if model_version:
        payload["modelVersion"] = model_version
    if response_id:
        payload["responseId"] = response_id
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def format_sse_usage(
    final_usage: dict[str, object] | None,
    *,
    finish_reason: str = "STOP",
    model_version: str | None = None,
    response_id: str | None = None,
) -> str:
    """Format final completion usage metadata into SSE chunk string."""
    effective_usage = final_usage or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    usage_dict = to_gemini_usage_metadata(effective_usage).model_dump(
        mode="json", exclude_none=True
    )
    payload: dict[str, object] = {
        "candidates": [
            {
                "content": {"role": "model", "parts": []},
                "finishReason": finish_reason,
                "index": 0,
            }
        ],
        "usageMetadata": usage_dict,
    }
    if model_version:
        payload["modelVersion"] = model_version
    if response_id:
        payload["responseId"] = response_id
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def format_sse_error(exc: Exception) -> str:
    """Format exception into standard Gemini SSE error chunk."""
    code, msg, status_str = classify_gemini_error_payload(exc)
    return (
        "data: "
        + json.dumps(
            {
                "error": {
                    "code": code,
                    "message": msg,
                    "status": status_str,
                }
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )


def _build_gemini_streaming_response(
    *,
    client: AIStudioClient,
    req: GeminiGenerateContentRequest,
    model_path: str,
) -> StreamingResponse:
    async def stream_response():
        normalized = None
        try:
            await ensure_active_account(0, model=model_path)
            normalized = normalize_gemini_request(req, model_path)
            logger.info(
                "Gemini 流式请求: model=%s, 对话轮数=%s",
                normalized.model,
                len(req.contents),
            )
            final_usage: dict[str, object] | None = None
            final_finish_reason: str = "STOP"
            latest_response_id: str | None = None
            buffered_thought_sig: str | None = None
            for stream_attempt in range(MAX_RETRIES):
                has_yielded_data = False
                await ensure_active_account(stream_attempt, model=model_path)
                normalized = normalize_gemini_request(req, model_path)
                try:
                    async for event_type, text in client.stream_generate_content(
                        model=normalized.model,
                        capture_prompt=normalized.capture_prompt,
                        capture_images=normalized.capture_images,
                        contents=normalized.contents,
                        system_instruction_content=normalized.system_instruction,
                        tools=normalized.tools,
                        tool_config=normalized.tool_config,
                        safety_settings=normalized.safety_settings,
                        temperature=normalized.temperature,
                        top_p=normalized.top_p,
                        top_k=normalized.top_k,
                        max_tokens=normalized.max_tokens,
                        generation_config_overrides=normalized.generation_config_overrides,
                        sanitize_plain_text=False,
                        force_refresh_capture=stream_attempt > 0,
                    ):
                        has_yielded_data = True
                        if event_type == "usage":
                            final_usage = text if isinstance(text, dict) else None
                        elif event_type == "finish_reason":
                            final_finish_reason = (
                                to_gemini_finish_reason(text)
                                if isinstance(text, int)
                                else str(text)
                            )
                        elif event_type == "response_id":
                            latest_response_id = str(text)
                        elif event_type == "thought_signature":
                            buffered_thought_sig = str(text)
                        else:
                            chunk = format_sse_event(
                                event_type,
                                text,
                                model_version=normalized.model,
                                response_id=latest_response_id,
                                thought_signature=buffered_thought_sig,
                            )
                            buffered_thought_sig = None
                            if chunk:
                                yield chunk
                    break
                except Exception as exc:
                    if await handle_attempt_exception(
                        exc,
                        attempt=stream_attempt,
                        model_path=model_path,
                        normalized_model=normalized.model if normalized else None,
                        client=client,
                        has_yielded_data=has_yielded_data,
                    ):
                        continue
                    raise

            target_model = normalized.model if normalized else model_path
            record_rotator_event("success", model=target_model)
            if normalized is not None:
                runtime_state.record(normalized.model, "success", final_usage)
            logger.info(
                "Gemini 流式完成: model=%s, 输入Tokens=%s, 输出Tokens=%s",
                target_model,
                final_usage.get("prompt_tokens") if final_usage else 0,
                final_usage.get("completion_tokens") if final_usage else 0,
            )
            yield format_sse_usage(
                final_usage,
                finish_reason=final_finish_reason,
                model_version=target_model,
                response_id=latest_response_id,
            )
        except Exception as exc:
            yield format_sse_error(exc)
        finally:
            if normalized is not None:
                cleanup_files(normalized.cleanup_paths)

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
