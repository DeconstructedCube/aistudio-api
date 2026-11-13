"""Gemini-compatible application service handlers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

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
    NormalizedGeminiRequest,
    normalize_gemini_request,
)
from aistudio_api.domain.errors import (
    AistudioError,
    AuthError,
    RequestError,
    SessionExpiredError,
    StaleSessionError,
    TransportError,
    UsageLimitExceeded,
    is_transient_rpc_error,
    is_transport_error,
    parse_upstream_grpc_error,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.client import (
    AIStudioClient,
    PreparedRequest,
)
from aistudio_api.infrastructure.gateway.session import SessionContext
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("api.gemini")


def _request_account(
    request_ctx: SessionContext | None,
) -> tuple[AccountMeta | None, str | None]:
    """按本次请求的凭据路径确定账号，缺少上下文时保留兼容行为。"""
    account_svc = runtime_state.account_service
    if account_svc is None:
        return None, None
    if request_ctx is not None and request_ctx.auth_file:
        account_id = Path(request_ctx.auth_file).parent.name
        return account_svc.get_account(account_id), account_id
    account = account_svc.get_active_account()
    return account, account.id if account else None


def _is_request_generation_current(request_ctx: SessionContext | None) -> bool:
    """认证结果只能影响生成该请求时使用的 Cookie 代际。"""
    account_svc = runtime_state.account_service
    if request_ctx is None or not request_ctx.auth_file or account_svc is None:
        return True
    return bool(
        account_svc.is_cookie_generation_current(
            Path(request_ctx.auth_file).parent.name, request_ctx.cookie_id
        )
    )


def _record_request_auth_error(model: str, request_ctx: SessionContext | None) -> None:
    """将认证处罚记到实际请求账号，避免并发切号后的全局账号错归因。"""
    if request_ctx is None:
        record_rotator_event("auth_error", model=model)
        return
    _, account_id = _request_account(request_ctx)
    if account_id and runtime_state.rotator is not None:
        runtime_state.rotator.record_auth_error(account_id, model=model)


async def _prepare_content_request(
    client: AIStudioClient,
    normalized: NormalizedGeminiRequest,
    prepared: PreparedRequest | None,
    *,
    force_recreate_signer: bool = False,
) -> PreparedRequest:
    """显式持有本次签名快照，普通与流式请求共用相同的上下文绑定。"""
    if prepared is not None:
        return prepared
    return await client.prepare_request(
        model=normalized.model,
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
        force_recreate_signer=force_recreate_signer,
    )


AUTH_403_MAX_RETRIES = (
    5  # 403 阶梯重试机制：前 2 次重放，后 2 次重新签名，最后 1 次切号
)

# 分钟级短冷却阈值：冷却时长不超过该值视为瞬时 RPM 限流，而非今日配额耗尽
SHORT_RATE_LIMIT_COOLDOWN_SECONDS = 600


class Auth403RetryManager:
    """403 阶梯重试管理器：前 2 次重放，后 2 次重新签名，最后 1 次切号。

    force_recreate_signer 用于通知本地/远程签名服务销毁僵死热 Worker 冷重建，
    由处理入口在当次请求中消费后立即归零，避免重复触发昂贵的冷启动。
    """

    def __init__(self, max_retries: int = AUTH_403_MAX_RETRIES) -> None:
        self.count: int = 0
        self.max_retries: int = max_retries
        self.current_prepared: PreparedRequest | None = None
        self.force_recreate_signer: bool = False

    async def handle_403(
        self,
        exc: Exception,
        *,
        model: str,
        client: AIStudioClient,
        has_yielded_data: bool = False,
    ) -> bool:
        request_ctx = self.current_prepared.ctx if self.current_prepared else None
        if not _is_request_generation_current(request_ctx):
            self.current_prepared = None
            if not has_yielded_data:
                return True
            raise HTTPException(
                503, detail="Request credentials were replaced."
            ) from exc
        if has_yielded_data:
            raise HTTPException(
                403, detail={"message": str(exc), "type": "auth_error"}
            ) from exc

        self.count += 1
        _record_request_auth_error(model, request_ctx)

        # 1. 前 2 次 403: 重放已签名请求
        if self.count <= 2:
            # 本次快照由请求处理器绑定，不读取客户端共享的最近请求。
            self.force_recreate_signer = False
            logger.warning(
                "HTTP 403 重试 (%d/2 重放): model=%s",
                self.count,
                model,
            )
            return True

        # 2. 后 2 次 403: 尝试轮换 Cookie 并重新生成签名
        if self.count <= 4:
            self.current_prepared = None
            if self.count == 3:
                account_svc = runtime_state.account_service
                active_acc, _ = _request_account(request_ctx)
                if (
                    account_svc is not None
                    and active_acc is not None
                    and hasattr(account_svc, "rotate_account_cookies")
                ):
                    try:
                        session = getattr(client, "session", None)
                        result = await account_svc.rotate_account_cookies(
                            active_acc.id, session
                        )
                        if result.get(
                            "stale"
                        ) is True or not _is_request_generation_current(request_ctx):
                            return True
                    except Exception:
                        pass
            self.force_recreate_signer = self.count == 3
            logger.warning(
                "HTTP 403 重试 (%d/2 重签): model=%s, force_recreate=%s",
                self.count - 2,
                model,
                self.force_recreate_signer,
            )
            return True

        # 3. 第 5 次 403: 切换备用账号
        if self.count == 5:
            self.current_prepared = None
            self.force_recreate_signer = True
            _, failed_id = _request_account(request_ctx)
            if await try_switch_account(
                model=model,
                failed_account_id=failed_id,
                expected_cookie_id=request_ctx.cookie_id if request_ctx else None,
            ):
                logger.warning(
                    "HTTP 403 切换备用账号重试: model=%s",
                    model,
                )
                return True
            logger.error("HTTP 403 且无可用备用账号，停止重试: model=%s", model)
            raise HTTPException(
                403, detail={"message": str(exc), "type": "auth_error"}
            ) from exc

        raise HTTPException(
            403, detail={"message": str(exc), "type": "auth_error"}
        ) from exc


WIRE_TO_GEMINI_FINISH_REASON: dict[int, str] = {
    0: "FINISH_REASON_UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "OTHER",
    6: "LANGUAGE",
    7: "BLOCKLIST",
    8: "PROHIBITED_CONTENT",
    9: "SPII",
    10: "MALFORMED_FUNCTION_CALL",
    11: "IMAGE_SAFETY",
    12: "UNEXPECTED_TOOL_CALL",
    13: "TOO_MANY_TOOL_CALLS",
    14: "IMAGE_PROHIBITED_CONTENT",
    15: "IMAGE_OTHER",
    16: "NO_IMAGE",
    17: "IMAGE_RECITATION",
    18: "MISSING_THOUGHT_SIGNATURE",
}


def to_gemini_finish_reason(wire_code: int | str | None) -> str:
    if wire_code is None:
        return "STOP"
    if isinstance(wire_code, str):
        clean = wire_code.strip().upper()
        if not clean:
            return "STOP"
        if clean.isdigit():
            code_num = int(clean)
            if code_num == 22:
                return "STOP"
            return WIRE_TO_GEMINI_FINISH_REASON.get(
                code_num, "FINISH_REASON_UNSPECIFIED"
            )
        return clean
    if wire_code == 22:
        return "STOP"
    return WIRE_TO_GEMINI_FINISH_REASON.get(wire_code, "FINISH_REASON_UNSPECIFIED")


def clean_upstream_error_message(raw_msg: str) -> str:
    """Extract a clean, readable error message from Google/JSPB upstream error strings."""
    if not raw_msg:
        return ""
    text = raw_msg.strip()
    # 若不包含 JSPB/batchexecute 特征结构，直接保留原始错误文本
    if "[,[" not in text and "[null,[" not in text and not text.startswith("[,"):
        return text
    inner = text
    while True:
        m = re.match(r"^HTTP\s+\d+:\s*(.*)$", inner)
        if m:
            inner = m.group(1).strip()
        else:
            break
    _, msg = parse_upstream_grpc_error(inner)
    return msg or inner


def is_daily_quota_message(message: str) -> bool:
    """判断上游清理后的错误消息是否明确指向“日配额耗尽”。"""
    lowered = (message or "").casefold()
    return any(
        marker in lowered for marker in ("daily", "per day", "per_day", "perday")
    )


def active_account_cooldown_remaining(account_id: str | None, model: str) -> float:
    """查询账号在目标模型上的剩余冷却秒数，用于区分瞬时 RPM 限流与日配额耗尽。"""
    if not account_id:
        return 0.0
    rotator = runtime_state.rotator
    if rotator is None:
        return 0.0
    try:
        stats = rotator.get_account_stats(account_id)
        if stats is None:
            return 0.0
        return float(stats.get_cooldown_remaining(model))
    except Exception:
        return 0.0


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
            502: "UNAVAILABLE",
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
            502: "UNAVAILABLE",
            503: "UNAVAILABLE",
        }
        return (
            status,
            clean_msg,
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )
    if isinstance(exc, TransportError):
        return 502, str(exc), "UNAVAILABLE"
    if isinstance(exc, AistudioError):
        return 500, str(exc), "INTERNAL"
    return 500, str(exc), "INTERNAL"


async def handle_attempt_exception(
    exc: Exception,
    *,
    attempt: int,
    model_path: str,
    normalized_model: str | None,
    request_ctx: SessionContext | None = None,
    has_yielded_data: bool = False,
) -> bool:
    """Handle per-attempt error classification and account switching.

    Returns True if the operation should be retried, or raises HTTPException / re-raises.
    """
    target_model = normalized_model or model_path
    if isinstance(exc, StaleSessionError):
        if not has_yielded_data:
            logger.info("请求凭据代际已更新，重新准备请求: model=%s", target_model)
            return True
        raise HTTPException(503, detail="Request credentials were replaced.") from exc
    account_svc = runtime_state.account_service
    active_acc, failed_id = _request_account(request_ctx)
    if isinstance(exc, AuthError) and not _is_request_generation_current(request_ctx):
        if not has_yielded_data:
            logger.info("忽略旧 Cookie 代际的认证结果: account=%s", failed_id)
            return True
        raise HTTPException(503, detail="Request credentials were replaced.") from exc

    if isinstance(exc, ValueError):
        raise HTTPException(
            400, detail={"message": str(exc), "type": "bad_request"}
        ) from exc

    if isinstance(exc, SessionExpiredError):
        if not has_yielded_data and account_svc is not None and active_acc is not None:
            # 遇到 401 时先探活会话并尝试轮换滚动 Cookie，若拿到新 PSIDTS 则原地重试
            try:
                from aistudio_api.infrastructure.account.cookie_parser import (
                    load_cookies_from_auth_file,
                    probe_aistudio_session,
                )

                auth_path = account_svc.get_account_auth_path(active_acc.id)
                cdict = load_cookies_from_auth_file(auth_path) if auth_path else {}
                before_ts = cdict.get("__Secure-1PSIDTS", "")
                if cdict:
                    alive, _ = await probe_aistudio_session(cdict)
                    if not _is_request_generation_current(request_ctx):
                        return True
                    if alive and hasattr(account_svc, "rotate_account_cookies"):
                        session = (
                            runtime_state.client.session
                            if runtime_state.client
                            else None
                        )
                        rot_res = await account_svc.rotate_account_cookies(
                            active_acc.id, session
                        )
                        if rot_res.get(
                            "stale"
                        ) is True or not _is_request_generation_current(request_ctx):
                            return True
                        new_cookies = rot_res.get("cookies")
                        after_ts = (
                            new_cookies.get("__Secure-1PSIDTS", "")
                            if isinstance(new_cookies, dict)
                            else ""
                        )
                        if rot_res.get("ok") and after_ts and after_ts != before_ts:
                            logger.info(
                                "账号 %s 触发 401 后完成 Cookie 轮换，原地重试",
                                active_acc.id,
                            )
                            return True
            except Exception as rot_err:
                logger.debug("401 Cookie 轮换探测异常: %s", rot_err)

        if not _is_request_generation_current(request_ctx):
            return True
        logger.warning("账号登录态已失效: account=%s, %s", failed_id, exc)
        if not has_yielded_data and await try_switch_account(
            model=target_model,
            failed_account_id=failed_id,
            is_session_expired=True,
            expected_cookie_id=request_ctx.cookie_id if request_ctx else None,
        ):
            logger.info("已切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        raise HTTPException(
            401,
            detail={
                "message": "All accounts encountered login redirect or expired sessions. Please import fresh cookies.",
                "type": "auth_error",
            },
        ) from exc

    if isinstance(exc, AuthError):
        logger.warning("账号权限被拒绝 (HTTP 403): %s", exc)
        _record_request_auth_error(target_model, request_ctx)

        raise HTTPException(
            403, detail={"message": str(exc), "type": "auth_error"}
        ) from exc

    if isinstance(exc, UsageLimitExceeded):
        runtime_state.record(target_model, "rate_limited")
        record_rotator_event("rate_limited", model=target_model)
        upstream_detail = clean_upstream_error_message(str(exc))
        logger.warning(
            "账号触发 429 限流: model=%s, detail=%s", target_model, upstream_detail
        )
        if not has_yielded_data and await try_switch_account(
            model=target_model, failed_account_id=failed_id
        ):
            logger.warning(
                "触发 429 限流，切换账号重试 (%d/%d): model=%s",
                attempt + 1,
                MAX_RETRIES,
                target_model,
            )
            return True
        cooldown_remaining = active_account_cooldown_remaining(failed_id, target_model)
        is_daily_quota = (
            is_daily_quota_message(upstream_detail)
            or cooldown_remaining > SHORT_RATE_LIMIT_COOLDOWN_SECONDS
        )
        if is_daily_quota:
            message = (
                f"All accounts reached daily quota for model '{target_model}'. "
                "Quota resets at 00:00 PST."
            )
        else:
            retry_hint = (
                f"retry after ~{int(cooldown_remaining)}s"
                if cooldown_remaining > 0
                else "retry shortly"
            )
            message = (
                f"Rate limit exceeded (HTTP 429, {retry_hint}): "
                f"{upstream_detail or 'upstream quota exhausted'}"
            )
        logger.warning(
            "模型 %s 所有账号均在冷却中 (cooldown_remaining=%ds, daily_quota=%s)",
            target_model,
            int(cooldown_remaining),
            is_daily_quota,
        )
        raise HTTPException(
            429,
            detail={"message": message, "type": "rate_limit_exceeded"},
        ) from exc

    if (
        isinstance(exc, RequestError)
        and exc.status == 204
        and attempt == 0
        and not has_yielded_data
    ):
        logger.warning("收到上游 204 空响应，立即重试")
        return True

    # 针对 Google 上游偶发且完全随机的 RPC 路由 404 (Ambiguous request for service '' and method '/GenerativeService.*')：
    # 此报错为上游端点偶发抖动，在未产生输出时当场立即重试（严禁切号以防浪费配额，其他 404 严格按原样报错抛出）
    if (
        is_transient_rpc_error(exc)
        and not has_yielded_data
        and attempt < MAX_RETRIES - 1
    ):
        logger.warning(
            "上游 404 路由抖动，立即重试 (%d/%d): %s",
            attempt + 1,
            MAX_RETRIES,
            clean_upstream_error_message(str(exc)),
        )
        return True
    if is_transport_error(exc):
        logger.warning(
            "传输或签名异常，切换账号重试 (%d/%d): %s",
            attempt + 1,
            MAX_RETRIES,
            exc,
        )
        if not has_yielded_data and await try_switch_account(
            model=target_model, failed_account_id=failed_id
        ):
            logger.info("已切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        if isinstance(exc, TransportError):
            runtime_state.record(target_model, "errors")
            record_rotator_event("error", model=target_model)
            logger.warning("上游传输中断 (502): %s", exc)
            raise HTTPException(
                502, detail={"message": str(exc), "type": "upstream_error"}
            ) from exc
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
        logger.warning(
            "上游返回请求错误: status=%d, message=%s", status_code, clean_msg
        )
        raise HTTPException(
            status_code, detail={"message": clean_msg, "type": error_type}
        ) from exc

    if isinstance(exc, AistudioError):
        runtime_state.record(target_model, "errors")
        record_rotator_event("error", model=target_model)
        logger.warning("服务端处理异常: %s", exc)
        raise HTTPException(
            500, detail={"message": str(exc), "type": "server_error"}
        ) from exc

    runtime_state.record(target_model, "errors")
    record_rotator_event("error", model=target_model)
    logger.error("请求处理发生未捕获异常: %s", exc)
    logger.debug("异常堆栈详情:", exc_info=True)
    raise HTTPException(
        500, detail={"message": str(exc), "type": "server_error"}
    ) from exc


async def _do_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
) -> GeminiGenerateContentResponse:
    last_error: Exception | None = None
    auth_manager = Auth403RetryManager()
    total_attempts = MAX_RETRIES + AUTH_403_MAX_RETRIES

    for attempt in range(total_attempts):
        await ensure_active_account(attempt, model=model_path)
        normalized = None
        request_ctx: SessionContext | None = None
        try:
            normalized = normalize_gemini_request(req, model_path)
            logger.info(
                "非流式请求: model=%s, 轮数=%d, 尝试=%d/%d",
                normalized.model,
                len(req.contents),
                attempt + 1,
                total_attempts,
            )
            force_recreate = auth_manager.force_recreate_signer
            auth_manager.force_recreate_signer = False
            prepared = await _prepare_content_request(
                client,
                normalized,
                auth_manager.current_prepared,
                force_recreate_signer=force_recreate,
            )
            request_ctx = prepared.ctx
            auth_manager.current_prepared = prepared
            output = await client.generate_content(
                model=normalized.model, prepared=prepared
            )
            auth_manager.current_prepared = None

            final_fr = to_gemini_finish_reason(
                output.candidates[0].finish_reason if output.candidates else None
            )
            record_rotator_event("success", model=normalized.model, usage=output.usage)
            runtime_state.record(
                normalized.model,
                "success",
                usage=output.usage,
                finish_reason=final_fr,
            )
            pt = output.usage.get("prompt_tokens") if output.usage else 0
            ct = output.usage.get("completion_tokens") if output.usage else 0
            logger.info(
                "非流式完成: model=%s, finish=%s, in=%s, out=%s",
                normalized.model,
                final_fr,
                pt,
                ct,
            )
            cand = output.candidates[0] if output.candidates else None
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
                                thinking_signature=cand.thinking_signature
                                if cand
                                else "",
                                text_signature=cand.text_signature if cand else "",
                                standalone_signature=(
                                    cand.standalone_signature if cand else ""
                                ),
                            ),
                        ),
                        finishReason=final_fr,
                        index=0,
                    )
                ],
                usageMetadata=to_gemini_usage_metadata(output.usage),
                modelVersion=normalized.model,
                responseId=output.response_id or None,
            )
        except Exception as exc:
            last_error = exc
            if isinstance(exc, AuthError) and not isinstance(exc, SessionExpiredError):
                target_m = normalized.model if normalized else model_path
                if await auth_manager.handle_403(exc, model=target_m, client=client):
                    continue
                raise
            if await handle_attempt_exception(
                exc,
                attempt=attempt,
                model_path=model_path,
                normalized_model=normalized.model if normalized else None,
                request_ctx=request_ctx,
            ):
                auth_manager.current_prepared = None
                continue
            raise

    raise HTTPException(
        429,
        detail={
            "message": f"All accounts reached daily quota for model '{model_path}'. Quota resets at 00:00 PST.",
            "type": "rate_limit_exceeded",
        },
    ) from last_error


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

    # 启动非流式生成任务并提供防 524 代理超时的保活机制 (NONSTREAM_KEEPALIVE)
    gen_task = asyncio.create_task(_do_generate_content(model_path, req, client))
    try:
        # 前 10 秒直接等待结果；绝大多数请求在此期间返回标准对象
        return await asyncio.wait_for(asyncio.shield(gen_task), timeout=10.0)
    except TimeoutError:
        logger.info(
            "非流式请求耗时超过 10s，激活 HTTP 换行保活流: model=%s", model_path
        )

        async def keepalive_stream():
            yield b"\n"
            try:
                while not gen_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(gen_task), timeout=10.0)
                        break
                    except TimeoutError:
                        yield b"\n"
                res = await gen_task
                yield res.model_dump_json(by_alias=True, exclude_none=True).encode(
                    "utf-8"
                )
            except Exception as exc:
                code, msg, status_str = classify_gemini_error_payload(exc)
                err_payload = {
                    "error": {
                        "code": code,
                        "message": msg,
                        "status": status_str,
                    }
                }
                yield json.dumps(err_payload, ensure_ascii=False).encode("utf-8")
            finally:
                if not gen_task.done():
                    gen_task.cancel()
                elif not gen_task.cancelled():
                    _ = gen_task.exception()

        return StreamingResponse(
            keepalive_stream(),
            media_type="application/json",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


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
        if thought_signature and parts and not parts[0].get("thoughtSignature"):
            parts[0]["thoughtSignature"] = thought_signature
    elif event_type == "images":
        img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", images=img_list)
        ]
        if thought_signature and parts and not parts[0].get("thoughtSignature"):
            parts[0]["thoughtSignature"] = thought_signature
    elif event_type == "reasoning_images":
        r_img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", reasoning_images=r_img_list)
        ]
        if thought_signature and parts and not parts[0].get("thoughtSignature"):
            parts[0]["thoughtSignature"] = thought_signature
    elif event_type == "thought_signature":
        parts = [{"thoughtSignature": str(text)}]
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
        stream_start_time = time.perf_counter()
        try:
            logger.info(
                "流式请求: model=%s, 轮数=%d",
                model_path,
                len(req.contents),
            )
            final_usage: dict[str, object] | None = None
            final_finish_reason: str = "STOP"
            latest_response_id: str | None = None
            buffered_thought_sig: str | None = None
            stream_completed = False
            last_stream_error: Exception | None = None
            auth_manager = Auth403RetryManager()
            total_attempts = MAX_RETRIES + AUTH_403_MAX_RETRIES
            for stream_attempt in range(total_attempts):
                has_yielded_data = False
                last_stream_error = None
                final_usage = None
                final_finish_reason = "STOP"
                latest_response_id = None
                buffered_thought_sig = None
                await ensure_active_account(stream_attempt, model=model_path)
                normalized = normalize_gemini_request(req, model_path)
                request_ctx = None
                try:
                    force_recreate = auth_manager.force_recreate_signer
                    auth_manager.force_recreate_signer = False
                    prepared = await _prepare_content_request(
                        client,
                        normalized,
                        auth_manager.current_prepared,
                        force_recreate_signer=force_recreate,
                    )
                    request_ctx = prepared.ctx
                    auth_manager.current_prepared = prepared
                    async for event_type, text in client.stream_generate_content(
                        model=normalized.model, prepared=prepared
                    ):
                        if event_type == "usage":
                            final_usage = text if isinstance(text, dict) else None
                        elif event_type == "finish_reason":
                            if text not in (None, 0, 22):
                                final_finish_reason = (
                                    to_gemini_finish_reason(text)
                                    if isinstance(text, int)
                                    else str(text)
                                )
                        elif event_type == "response_id":
                            latest_response_id = str(text)
                        elif event_type == "thought_signature":
                            buffered_thought_sig = str(text)
                        elif event_type == "done":
                            continue
                        else:
                            chunk = format_sse_event(
                                event_type,
                                text,
                                model_version=normalized.model,
                                response_id=latest_response_id,
                                thought_signature=buffered_thought_sig,
                            )
                            if chunk:
                                buffered_thought_sig = None
                                if not has_yielded_data:
                                    ttft_ms = (
                                        time.perf_counter() - stream_start_time
                                    ) * 1000
                                    logger.info(
                                        "流式首包到达: model=%s, ttft=%.1fms",
                                        normalized.model,
                                        ttft_ms,
                                    )
                                has_yielded_data = True
                                yield chunk
                    if buffered_thought_sig:
                        chunk = format_sse_event(
                            "thought_signature",
                            buffered_thought_sig,
                            model_version=normalized.model,
                            response_id=latest_response_id,
                        )
                        buffered_thought_sig = None
                        if chunk:
                            has_yielded_data = True
                            yield chunk
                    auth_manager.current_prepared = None
                    stream_completed = True
                    break
                except Exception as exc:
                    last_stream_error = exc
                    if (
                        isinstance(exc, AuthError)
                        and not isinstance(exc, SessionExpiredError)
                        and not has_yielded_data
                    ):
                        target_m = normalized.model if normalized else model_path
                        if await auth_manager.handle_403(
                            exc,
                            model=target_m,
                            client=client,
                            has_yielded_data=has_yielded_data,
                        ):
                            continue
                        raise
                    if await handle_attempt_exception(
                        exc,
                        attempt=stream_attempt,
                        model_path=model_path,
                        normalized_model=normalized.model if normalized else None,
                        request_ctx=request_ctx,
                        has_yielded_data=has_yielded_data,
                    ):
                        auth_manager.current_prepared = None
                        continue
                    raise

            if not stream_completed and last_stream_error is not None:
                raise last_stream_error
            target_model = normalized.model if normalized else model_path
            record_rotator_event("success", model=target_model, usage=final_usage)
            if normalized is not None:
                runtime_state.record(
                    normalized.model,
                    "success",
                    usage=final_usage,
                    finish_reason=final_finish_reason,
                )
            elapsed_total_ms = (time.perf_counter() - stream_start_time) * 1000
            pt = final_usage.get("prompt_tokens") if final_usage else 0
            ct = final_usage.get("completion_tokens") if final_usage else 0
            logger.info(
                "流式完成: model=%s, 耗时=%.1fms, finish=%s, in=%s, out=%s",
                target_model,
                elapsed_total_ms,
                final_finish_reason,
                pt,
                ct,
            )
            yield format_sse_usage(
                final_usage,
                finish_reason=final_finish_reason,
                model_version=target_model,
                response_id=latest_response_id,
            )
        except Exception as exc:
            logger.warning("流式生成异常: model=%s, error=%s", model_path, exc)
            yield format_sse_error(exc)

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
