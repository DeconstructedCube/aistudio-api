"""Gemini-compatible application service handlers."""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException
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

logger = logging.getLogger("aistudio.server")


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
    if isinstance(exc, ValueError):
        return 400, str(exc), "INVALID_ARGUMENT"
    if isinstance(exc, HTTPException):
        status = exc.status_code
        detail = exc.detail
        raw_msg = detail.get("message") if isinstance(detail, dict) else detail
        msg = str(raw_msg) if raw_msg is not None else ""
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
            msg,
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )
    if isinstance(exc, RequestError):
        status = exc.status if exc.status > 0 else 500
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
            str(exc),
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )

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
        logger.warning("Gemini 账号 Session 已失效（重定向至登录页）: %s", exc)
        record_rotator_event("auth_error", model=target_model)
        if not has_yielded_data and await try_switch_account(
            model=target_model, failed_account_id=failed_id, is_auth_error=True
        ):
            logger.info("已自动切换至健康账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        raise HTTPException(
            401,
            detail={
                "message": "All accounts have expired sessions. Please import fresh cookies.",
                "type": "auth_error",
            },
        ) from exc

    if isinstance(exc, AuthError):
        logger.warning("Gemini 403 权限拒绝: %s", exc)
        client.clear_snapshot_cache()
        record_rotator_event("auth_error", model=target_model)
        if not has_yielded_data and await try_switch_account(
            model=target_model, failed_account_id=failed_id, is_auth_error=True
        ):
            logger.info("已自动切换至可用账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
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
                "Gemini 429 配额耗尽: model=%s，已切换至可用账号重试 (%d/%d)",
                target_model,
                attempt + 1,
                MAX_RETRIES,
            )
            return True
        logger.warning(
            "Gemini 429 限额: model=%s，全部账号今日配额均已耗尽", target_model
        )
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
        logger.warning("Gemini 收到 204，清理 snapshot 缓存后重试一次")
        client.clear_snapshot_cache()
        return True

    if isinstance(exc, RuntimeError):
        err_msg = str(exc).lower()
        if (
            (
                "cdp" in err_msg
                or "closed" in err_msg
                or "aborted" in err_msg
                or "timeout" in err_msg
            )
            and attempt < 2
            and not has_yielded_data
        ):
            logger.warning(
                "Gemini 浏览器进程断开或超时，自动重启并重试 (%d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                exc,
            )
            if client._session is not None:
                await client._session._close_internal()
            return True

    if isinstance(exc, AistudioError):
        runtime_state.record(target_model, "errors")
        record_rotator_event("error", model=target_model)
        logger.warning("Gemini error: %s", exc)
        raise HTTPException(
            500, detail={"message": str(exc), "type": "server_error"}
        ) from exc

    runtime_state.record(target_model, "errors")
    record_rotator_event("error", model=target_model)
    logger.error("Gemini unexpected error: %s", exc)
    logger.debug("Gemini error details:", exc_info=True)
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
                "Gemini: model=%s, contents=%s, stream=False, attempt=%d",
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
                        finishReason="STOP",
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


def format_sse_event(event_type: str, text: object) -> str | None:
    """Format a single Gemini streaming event into SSE chunk string."""
    if not text:
        return None
    if event_type == "body":
        safe_text = json.dumps(text, ensure_ascii=False)
        return f'data: {{"candidates": [{{"content": {{"role": "model", "parts": [{{"text": {safe_text}}}]}}, "index": 0}}]}}\n\n'
    if event_type == "tool_calls":
        fc_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", function_calls=fc_list)
        ]
        payload = {
            "candidates": [{"content": {"role": "model", "parts": parts}, "index": 0}]
        }
        return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    if event_type == "thought_signature":
        safe_sig = json.dumps(str(text), ensure_ascii=False)
        return f'data: {{"candidates": [{{"content": {{"role": "model", "parts": [{{"thoughtSignature": {safe_sig}}}]}}, "index": 0}}]}}\n\n'
    if event_type == "images":
        img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", images=img_list)
        ]
        payload = {
            "candidates": [{"content": {"role": "model", "parts": parts}, "index": 0}]
        }
        return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    if event_type == "reasoning_images":
        r_img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", reasoning_images=r_img_list)
        ]
        payload = {
            "candidates": [{"content": {"role": "model", "parts": parts}, "index": 0}]
        }
        return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    if event_type == "thinking":
        safe_text = json.dumps(text, ensure_ascii=False)
        return f'data: {{"candidates": [{{"content": {{"role": "model", "parts": [{{"text": {safe_text}, "thought": true}}]}}, "index": 0}}]}}\n\n'
    return None


def format_sse_usage(final_usage: dict[str, object] | None) -> str:
    """Format final completion usage metadata into SSE chunk string."""
    effective_usage = final_usage or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    usage_json = to_gemini_usage_metadata(effective_usage).model_dump_json()
    return f'data: {{"candidates": [{{"content": {{"role": "model", "parts": []}}, "finishReason": "STOP", "index": 0}}], "usageMetadata": {usage_json}}}\n\n'


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
                "Gemini stream: model=%s, contents=%s",
                normalized.model,
                len(req.contents),
            )
            final_usage: dict[str, object] | None = None
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
                        else:
                            chunk = format_sse_event(event_type, text)
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
            yield format_sse_usage(final_usage)
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
