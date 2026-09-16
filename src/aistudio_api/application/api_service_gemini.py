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
from aistudio_api.application.api_service_common import (
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
    UsageLimitExceeded,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient

logger = logging.getLogger("aistudio.server")


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

    last_error = None
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
                        finishReason=(
                            "STOP"
                            if not output.function_calls
                            else "FUNCTION_CALL"
                        ),
                    )
                ],
                usageMetadata=to_gemini_usage_metadata(output.usage),
            )
        except ValueError as exc:
            raise HTTPException(
                400, detail={"message": str(exc), "type": "bad_request"}
            ) from exc
        except UsageLimitExceeded as exc:
            target_model = normalized.model if normalized else model_path
            runtime_state.record(target_model, "rate_limited")
            last_error = exc
            account_svc = runtime_state.account_service
            active_acc = (
                account_svc.get_active_account() if account_svc else None
            )
            failed_id = active_acc.id if active_acc else None

            record_rotator_event("rate_limited", model=target_model)
            if await try_switch_account(
                model=target_model, failed_account_id=failed_id
            ):
                logger.info(
                    "Gemini 429 配额耗尽: model=%s，已切换至可用账号重试 (%d/%d)",
                    target_model,
                    attempt + 1,
                    MAX_RETRIES,
                )
                continue
            logger.warning("Gemini 429 限额: model=%s，全部账号今日配额均已耗尽", target_model)
            raise HTTPException(
                429,
                detail={
                    "message": f"All accounts reached daily quota for model '{target_model}'. Quota resets at 00:00 PST.",
                    "type": "rate_limit_exceeded",
                },
            ) from exc
        except AistudioError as exc:
            target_model = normalized.model if normalized else model_path
            runtime_state.record(target_model, "errors")
            record_rotator_event("error", model=target_model)
            logger.warning("Gemini error: %s", exc)
            raise HTTPException(
                500, detail={"message": str(exc), "type": "server_error"}
            ) from exc
        except Exception as exc:
            target_model = normalized.model if normalized else model_path
            runtime_state.record(target_model, "errors")
            record_rotator_event("error", model=target_model)
            logger.error("Gemini unexpected error: %s", exc)
            logger.debug("Gemini error details:", exc_info=True)
            raise HTTPException(
                500, detail={"message": str(exc), "type": "server_error"}
            ) from exc
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
            final_usage = None
            for stream_attempt in range(MAX_RETRIES):
                has_yielded_data = False
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
                        if event_type == "body" and text:
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "candidates": [
                                            {
                                                "content": {
                                                    "role": "model",
                                                    "parts": [{"text": text}],
                                                },
                                                "finishReason": None,
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n\n"
                            )
                        elif event_type == "images" and text:
                            img_list = text if isinstance(text, list) else []
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "candidates": [
                                            {
                                                "content": {
                                                    "role": "model",
                                                    "parts": [
                                                        part.model_dump(
                                                            mode="json",
                                                            exclude_none=True,
                                                        )
                                                        for part in to_gemini_parts(
                                                            "", images=img_list
                                                        )
                                                    ],
                                                },
                                                "finishReason": None,
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n\n"
                            )
                        elif event_type == "reasoning_images" and text:
                            r_img_list = (
                                text if isinstance(text, list) else []
                            )
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "candidates": [
                                            {
                                                "content": {
                                                    "role": "model",
                                                    "parts": [
                                                        part.model_dump(
                                                            mode="json",
                                                            exclude_none=True,
                                                        )
                                                        for part in to_gemini_parts(
                                                            "",
                                                            reasoning_images=r_img_list,
                                                        )
                                                    ],
                                                },
                                                "finishReason": None,
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n\n"
                            )
                        elif event_type == "thinking" and text:
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "candidates": [
                                            {
                                                "content": {
                                                    "role": "model",
                                                    "parts": [
                                                        {
                                                            "text": text,
                                                            "thought": True,
                                                        }
                                                    ],
                                                },
                                                "finishReason": None,
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n\n"
                            )
                        elif event_type == "usage":
                            final_usage = (
                                text if isinstance(text, dict) else None
                            )
                    break
                except UsageLimitExceeded as exc:
                    target_model = (
                        normalized.model if normalized else model_path
                    )
                    runtime_state.record(target_model, "rate_limited")
                    account_svc = runtime_state.account_service
                    active_acc = (
                        account_svc.get_active_account()
                        if account_svc
                        else None
                    )
                    failed_id = active_acc.id if active_acc else None
                    record_rotator_event("rate_limited", model=target_model)
                    if not has_yielded_data and await try_switch_account(
                        model=target_model, failed_account_id=failed_id
                    ):
                        logger.warning(
                            "Gemini stream 429 限流: model=%s，已切换账号重试 (%d/%d)",
                            target_model,
                            stream_attempt + 1,
                            MAX_RETRIES,
                        )
                        continue
                    raise
                except RequestError as exc:
                    if exc.status == 204 and stream_attempt == 0:
                        logger.warning(
                            "Gemini stream 收到 204，清理 snapshot 缓存后重试一次"
                        )
                        client.clear_snapshot_cache()
                        continue
                    raise
                except AuthError as exc:
                    if stream_attempt == 0:
                        logger.warning(
                            "Gemini stream 鉴权异常，清理 snapshot 缓存后重试一次: %s",
                            exc,
                        )
                        client.clear_snapshot_cache()
                        continue
                    raise
            record_rotator_event(
                "success", model=normalized.model if normalized else model_path
            )
            if normalized is not None:
                runtime_state.record(normalized.model, "success", final_usage)
            if final_usage:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "candidates": [],
                            "usageMetadata": to_gemini_usage_metadata(
                                final_usage
                            ).model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"
        except Exception as exc:
            target_model = normalized.model if normalized else model_path
            if not isinstance(exc, UsageLimitExceeded):
                record_rotator_event("error", model=target_model)
            runtime_state.record(target_model, "errors")
            if isinstance(exc, AistudioError):
                logger.warning("Gemini stream error: %s", exc)
            else:
                logger.error("Gemini stream unexpected error: %s", exc)
                logger.debug("Gemini stream error details:", exc_info=True)
            yield (
                "data: "
                + json.dumps(
                    {"error": {"message": str(exc)}}, ensure_ascii=False
                )
                + "\n\n"
            )
        finally:
            if normalized is not None:
                cleanup_files(normalized.cleanup_paths)

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
