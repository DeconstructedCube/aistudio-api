"""Gemini-compatible application service handlers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path
from typing import Literal

from anyio import CancelScope
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from aistudio_api.api.response_models import (
    GeminiCandidateResponse,
    GeminiContentResponse,
    GeminiCountTokensResponse,
    GeminiCreateDecisionResponse,
    GeminiDecisionModalityUsage,
    GeminiDecisionUsageResponse,
    GeminiGenerateContentResponse,
)
from aistudio_api.api.responses import to_gemini_parts, to_gemini_usage_metadata
from aistudio_api.api.schemas import (
    GeminiCountTokensRequest,
    GeminiCreateDecisionRequest,
    GeminiGenerateContentRequest,
)
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    MAX_RETRIES,
    ensure_active_account,
    get_max_retries,
    record_rotator_event,
    try_switch_account,
)
from aistudio_api.application.chat_service import (
    NormalizedGeminiRequest,
    normalize_gemini_request,
)
from aistudio_api.domain.errors import (
    AccountRestrictedError,
    AccountVerificationRequiredError,
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
from aistudio_api.infrastructure.utils.logger import (
    get_logger,
    set_current_request_account,
)

logger = get_logger("api.gemini")


def _effective_max_retries() -> int:
    if MAX_RETRIES != 5:
        return max(1, int(MAX_RETRIES))
    return get_max_retries()


def _request_context_snapshot(client: AIStudioClient) -> SessionContext:
    """在签名前绑定本次账号，未知来源用空上下文阻止回退到新活跃账号。"""
    session = getattr(client, "session", None)
    context = getattr(session, "active_context", None) if session is not None else None
    return context if isinstance(context, SessionContext) else SessionContext()


def _request_account(
    request_ctx: SessionContext | None,
) -> tuple[AccountMeta | None, str | None]:
    """按本次请求凭据定位账号，显式未知来源不能落到新的全局账号。"""
    account_svc = runtime_state.account_service
    if account_svc is None:
        return None, None
    if request_ctx is not None:
        if not request_ctx.auth_file:
            return None, None
        account_id = Path(request_ctx.auth_file).parent.name
        acc = account_svc.get_account(account_id)
        set_current_request_account(
            account_id,
            getattr(acc, "auth_user", None) if acc else request_ctx.auth_user,
        )
        return acc, account_id
    account = account_svc.get_active_account()
    if account is not None:
        set_current_request_account(
            getattr(account, "id", None), getattr(account, "auth_user", "0")
        )
    return account, getattr(account, "id", None) if account else None


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


def _record_request_rotator_event(
    event: Literal["auth_error", "rate_limited", "error", "success"],
    *,
    model: str,
    request_ctx: SessionContext | None,
    usage: dict[str, object] | None = None,
) -> None:
    """只记录本次请求的当前代际账号，未知或已删除来源不影响新活跃账号。"""
    if request_ctx is None:
        record_rotator_event(event, model=model, usage=usage)
        return
    if not request_ctx.auth_file:
        return
    account, account_id = _request_account(request_ctx)
    rotator = runtime_state.rotator
    if (
        account is None
        or account_id is None
        or rotator is None
        or not _is_request_generation_current(request_ctx)
    ):
        return
    record_rotator_event(event, model=model, usage=usage, account_id=account_id)


def _is_prepared_binding_current(prepared: PreparedRequest) -> bool:
    """重放缓存签名时核对账户启用状态、代际、登录索引与订阅权益。"""
    account_svc = runtime_state.account_service
    if account_svc is None:
        return True
    ctx = prepared.ctx
    account = account_svc.get_active_account()
    if (
        ctx is None
        or not ctx.auth_file
        or account is None
        or not account.enabled
        or Path(ctx.auth_file).parent.name != account.id
        or not _is_request_generation_current(ctx)
        or ctx.auth_user != account.auth_user
    ):
        return False
    from aistudio_api.infrastructure.account.cookie_parser import normalize_benefit_tier

    return normalize_benefit_tier(ctx.tier) == normalize_benefit_tier(account.tier)


async def _prepare_content_request(
    client: AIStudioClient,
    normalized: NormalizedGeminiRequest,
    prepared: PreparedRequest | None,
    *,
    force_recreate_signer: bool = False,
    ctx: SessionContext | None = None,
) -> PreparedRequest:
    """显式持有本次签名快照，普通与流式请求共用相同的上下文绑定。"""
    if prepared is not None and not force_recreate_signer:
        if _is_prepared_binding_current(prepared):
            return prepared
        logger.info("缓存签名的账户绑定已变化，重新准备请求")
    if isinstance(ctx, SessionContext):
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
            ctx=ctx,
        )
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

    force_recreate_signer 用于通知本地 WAA IPC 签名器销毁僵死热 Worker 冷重建，
    由处理入口在当次请求中消费后立即归零，避免重复触发昂贵的冷启动。
    """

    def __init__(self, max_retries: int = AUTH_403_MAX_RETRIES) -> None:
        self.count: int = 0
        self.max_retries: int = max_retries
        self.current_prepared: PreparedRequest | None = None
        self.pinned_ctx: SessionContext | None = None
        self.force_recreate_signer: bool = False

    @staticmethod
    def _diagnostic_context(
        client: AIStudioClient,
        prepared: PreparedRequest | None,
        request_ctx: SessionContext | None,
    ) -> str:
        from aistudio_api.infrastructure.account.cookie_parser import (
            DEFAULT_BROWSER_USER_AGENT,
        )

        outer_ua = DEFAULT_BROWSER_USER_AGENT
        pool_state = ""
        if prepared is not None and isinstance(prepared.headers, dict):
            outer_ua = str(prepared.headers.get("User-Agent") or outer_ua)
        session = getattr(client, "session", None)
        signer = getattr(session, "_signer", None) if session is not None else None
        if signer is not None:
            pool_state = str(getattr(signer, "last_pool_state", "") or "")
        acc, acc_id = _request_account(request_ctx)
        auth_user = (
            acc.auth_user
            if acc is not None
            else (request_ctx.auth_user if request_ctx is not None else "0")
        )
        return (
            f"account={acc_id or 'unknown'}(u/{auth_user}), "
            f"signer=local_ipc, pool={pool_state or 'unknown'}, "
            f"ua={outer_ua[:48] or 'default'}"
        )

    async def handle_403(
        self,
        exc: Exception,
        *,
        model: str,
        client: AIStudioClient,
        request_ctx: SessionContext | None = None,
        has_yielded_data: bool = False,
    ) -> bool:
        if request_ctx is None and self.current_prepared is not None:
            request_ctx = self.current_prepared.ctx
        if not _is_request_generation_current(request_ctx):
            self.current_prepared = None
            self.pinned_ctx = None
            if not has_yielded_data:
                return True
            raise HTTPException(
                503, detail="Request credentials were replaced."
            ) from exc
        if has_yielded_data:
            raise HTTPException(
                403, detail={"message": str(exc), "type": "auth_error"}
            ) from exc

        diag = self._diagnostic_context(client, self.current_prepared, request_ctx)
        self.count += 1
        _record_request_rotator_event(
            "auth_error", model=model, request_ctx=request_ctx
        )

        # 1. 前 2 次 403: 重放已签名请求
        if self.count <= 2:
            # 本次快照由请求处理器绑定，不读取客户端共享的最近请求。
            self.force_recreate_signer = False
            logger.warning(
                "HTTP 403 重试 (%d/2 重放): model=%s, %s",
                self.count,
                model,
                diag,
            )
            return True

        # 2. 后 2 次 403: 尝试轮换 Cookie 并重新生成签名
        if self.count <= 4:
            self.current_prepared = None
            self.pinned_ctx = request_ctx
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
                            self.pinned_ctx = None
                            return True
                    except Exception:
                        pass
            self.force_recreate_signer = self.count == 3
            logger.warning(
                "HTTP 403 重试 (%d/2 重签): model=%s, force_recreate=%s, %s",
                self.count - 2,
                model,
                self.force_recreate_signer,
                diag,
            )
            return True

        # 3. 第 5 次 403: 切换备用账号
        if self.count == 5:
            self.current_prepared = None
            self.pinned_ctx = None
            self.force_recreate_signer = True
            _, failed_id = _request_account(request_ctx)
            if await try_switch_account(
                model=model,
                failed_account_id=failed_id,
                expected_cookie_id=request_ctx.cookie_id if request_ctx else None,
            ):
                logger.warning(
                    "HTTP 403 切换备用账号重试: model=%s, %s",
                    model,
                    diag,
                )
                return True
            logger.error(
                "HTTP 403 且无可用备用账号，停止重试: model=%s, %s", model, diag
            )
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
            504: "DEADLINE_EXCEEDED",
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
            504: "DEADLINE_EXCEEDED",
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
            max_retries = _effective_max_retries()
            logger.info("已切换账号重试 (%d/%d)", attempt + 1, max_retries)
            return True
        raise HTTPException(
            401,
            detail={
                "message": "All accounts encountered login redirect or expired sessions. Please import fresh cookies.",
                "type": "auth_error",
            },
        ) from exc

    if isinstance(exc, (AccountVerificationRequiredError, AccountRestrictedError)):
        if not _is_request_generation_current(request_ctx):
            if not has_yielded_data:
                logger.info("忽略旧 Cookie 代际的验证/受限结果: account=%s", failed_id)
                return True
            raise HTTPException(
                503, detail="Request credentials were replaced."
            ) from exc
        _record_request_rotator_event(
            "auth_error", model=target_model, request_ctx=request_ctx
        )
        if account_svc is not None and failed_id:
            disabled = account_svc.toggle_account_enabled(failed_id, False)
            auth_u = (
                active_acc.auth_user
                if active_acc is not None
                else (request_ctx.auth_user if request_ctx is not None else "0")
            )
            reason = (
                "需要身份验证"
                if isinstance(exc, AccountVerificationRequiredError)
                else "服务受限"
            )
            logger.error(
                "账号 %s (u/%s) %s，已自动禁用 (disabled=%s): %s",
                failed_id,
                auth_u,
                reason,
                disabled is not None and not disabled.enabled,
                exc,
            )
        if not has_yielded_data and await try_switch_account(
            model=target_model,
            failed_account_id=failed_id,
            expected_cookie_id=request_ctx.cookie_id if request_ctx else None,
        ):
            max_retries = _effective_max_retries()
            logger.info("已切换账号重试 (%d/%d)", attempt + 1, max_retries)
            return True
        raise HTTPException(
            403, detail={"message": str(exc), "type": "auth_error"}
        ) from exc

    if isinstance(exc, AuthError):
        logger.warning("账号权限被拒绝 (HTTP 403): %s", exc)
        _record_request_rotator_event(
            "auth_error", model=target_model, request_ctx=request_ctx
        )

        raise HTTPException(
            403, detail={"message": str(exc), "type": "auth_error"}
        ) from exc

    if isinstance(exc, UsageLimitExceeded):
        runtime_state.record(target_model, "rate_limited")
        _record_request_rotator_event(
            "rate_limited", model=target_model, request_ctx=request_ctx
        )
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
                _effective_max_retries(),
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
    max_retries = _effective_max_retries()
    if (
        is_transient_rpc_error(exc)
        and not has_yielded_data
        and attempt < max_retries - 1
    ):
        logger.warning(
            "上游 404 路由抖动，立即重试 (%d/%d): %s",
            attempt + 1,
            max_retries,
            clean_upstream_error_message(str(exc)),
        )
        return True
    if is_transport_error(exc):
        # 传输失败只能使用普通预算，最后一次失败或已发出内容时不再切号。
        if not has_yielded_data and attempt < max_retries - 1:
            logger.warning(
                "传输或签名异常，切换账号重试 (%d/%d): %s",
                attempt + 1,
                max_retries,
                exc,
            )
            if await try_switch_account(
                model=target_model, failed_account_id=failed_id
            ):
                logger.info("已切换账号重试 (%d/%d)", attempt + 1, max_retries)
                return True
        runtime_state.record(target_model, "errors")
        _record_request_rotator_event(
            "error", model=target_model, request_ctx=request_ctx
        )
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
        _record_request_rotator_event(
            "error", model=target_model, request_ctx=request_ctx
        )
        logger.warning("服务端处理异常: %s", exc)
        raise HTTPException(
            500, detail={"message": str(exc), "type": "server_error"}
        ) from exc

    runtime_state.record(target_model, "errors")
    _record_request_rotator_event("error", model=target_model, request_ctx=request_ctx)
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
    max_retries = _effective_max_retries()
    total_attempts = max_retries + AUTH_403_MAX_RETRIES
    ordinary_attempt = 0

    for attempt in range(total_attempts):
        if ordinary_attempt >= max_retries:
            break
        normalized = None
        request_ctx: SessionContext | None = None
        try:
            normalized = normalize_gemini_request(req, model_path)
            ensured_ctx = await ensure_active_account(attempt, model=model_path)
            effective_ctx = auth_manager.pinned_ctx or (
                ensured_ctx
                if isinstance(ensured_ctx, SessionContext)
                else _request_context_snapshot(client)
            )
            request_ctx = effective_ctx
            _request_account(request_ctx)
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
                ctx=effective_ctx,
            )
            request_ctx = prepared.ctx or effective_ctx
            auth_manager.current_prepared = prepared
            output = await client.generate_content(
                model=normalized.model, prepared=prepared
            )
            auth_manager.current_prepared = None

            final_fr = to_gemini_finish_reason(
                output.candidates[0].finish_reason if output.candidates else None
            )
            _record_request_rotator_event(
                "success",
                model=normalized.model,
                request_ctx=request_ctx,
                usage=output.usage,
            )
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
            if isinstance(exc, AuthError) and not isinstance(
                exc,
                (
                    SessionExpiredError,
                    AccountVerificationRequiredError,
                    AccountRestrictedError,
                ),
            ):
                target_m = normalized.model if normalized else model_path
                if await auth_manager.handle_403(
                    exc, model=target_m, client=client, request_ctx=request_ctx
                ):
                    continue
                raise
            if await handle_attempt_exception(
                exc,
                attempt=ordinary_attempt,
                model_path=model_path,
                normalized_model=normalized.model if normalized else None,
                request_ctx=request_ctx,
            ):
                # 403 单独计数，普通失败不能借用认证重试预算。
                ordinary_attempt += 1
                auth_manager.current_prepared = None
                auth_manager.pinned_ctx = None
                continue
            raise

    # 耗尽预算时保留最后一次故障类型，不能统一伪装成配额耗尽。
    error = last_error or TransportError("Request retry budget exhausted.")
    status_code, message, _ = classify_gemini_error_payload(error)
    raise HTTPException(status_code, detail={"message": message}) from error


async def _close_generation_task(
    task: asyncio.Task[GeminiGenerateContentResponse],
) -> None:
    # 响应结束时等待后台任务释放资源，任何清理错误都不能覆盖原响应错误。
    with CancelScope(shield=True):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class _RequestStreamingResponse(StreamingResponse):
    """在真实 ASGI 生命周期结束时关闭迭代器和非流式生成任务。"""

    def __init__(
        self,
        content: AsyncGenerator[str | bytes, None],
        *,
        media_type: str,
        generation_task: asyncio.Task[GeminiGenerateContentResponse] | None = None,
    ) -> None:
        super().__init__(
            content,
            media_type=media_type,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        self._iterator = content
        self._generation_task = generation_task

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 首响应头就断开时，尚未启动的 body iterator 不会执行自身 finally。
            with CancelScope(shield=True):
                try:
                    await self._iterator.aclose()
                finally:
                    if self._generation_task is not None:
                        await _close_generation_task(self._generation_task)


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
    except asyncio.CancelledError:
        # shield 不能让已断开的客户端留下后台生成任务。
        await _close_generation_task(gen_task)
        raise
    except TimeoutError:
        logger.info(
            "非流式请求耗时超过 10s，激活 HTTP 换行保活流: model=%s", model_path
        )

        async def keepalive_stream():
            try:
                yield b"\n"
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
                await _close_generation_task(gen_task)

        return _RequestStreamingResponse(
            keepalive_stream(),
            media_type="application/json",
            generation_task=gen_task,
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
    elif event_type == "images":
        img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", images=img_list)
        ]
    elif event_type == "reasoning_images":
        r_img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", reasoning_images=r_img_list)
        ]
    elif event_type == "thought_signature":
        parts = [{"thoughtSignature": str(text)}]
    else:
        return None

    if (
        thought_signature
        and parts
        and event_type in ("tool_calls", "images", "reasoning_images")
    ):
        existing_signature = parts[0].get("thoughtSignature")
        if not existing_signature:
            parts[0]["thoughtSignature"] = thought_signature
        elif existing_signature != thought_signature:
            # 独立签名不能覆盖工具或图片自己的签名，按原顺序保留独立 Part。
            parts.insert(0, {"thoughtSignature": thought_signature})
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


SSE_KEEPALIVE_INTERVAL = 15.0


async def _iter_events_with_keepalive(
    events: AsyncGenerator[tuple[str, object | None], None],
    *,
    interval: float = SSE_KEEPALIVE_INTERVAL,
) -> AsyncGenerator[tuple[str, object | None], None]:
    """Yield upstream events while emitting keepalive ticks on long silent pauses.

    Prevents downstream clients and reverse proxies from timing out during deep
    thinking or large buffered tool calls without cancelling the upstream reader.
    """
    it = events.__aiter__()

    async def _next_event() -> tuple[str, object | None]:
        return await it.__anext__()

    next_task: asyncio.Task[tuple[str, object | None]] | None = None
    try:
        while True:
            if next_task is None:
                next_task = asyncio.create_task(_next_event())
            try:
                item = await asyncio.wait_for(
                    asyncio.shield(next_task), timeout=interval
                )
                next_task = None
                yield item
            except TimeoutError:
                yield ("_keepalive", None)
            except StopAsyncIteration:
                next_task = None
                break
    finally:
        if next_task is not None:
            if not next_task.done():
                next_task.cancel()
            await asyncio.gather(next_task, return_exceptions=True)


def _build_gemini_streaming_response(
    *,
    client: AIStudioClient,
    req: GeminiGenerateContentRequest,
    model_path: str,
) -> StreamingResponse:
    async def stream_with_retries():
        normalized = None
        request_ctx: SessionContext | None = None
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
            max_retries = _effective_max_retries()
            total_attempts = max_retries + AUTH_403_MAX_RETRIES
            ordinary_attempt = 0
            for stream_attempt in range(total_attempts):
                if ordinary_attempt >= max_retries:
                    break
                has_yielded_data = False
                last_stream_error = None
                final_usage = None
                final_finish_reason = "STOP"
                latest_response_id = None
                buffered_thought_sig = None
                try:
                    normalized = normalize_gemini_request(req, model_path)
                    ensured_ctx = await ensure_active_account(
                        stream_attempt, model=model_path
                    )
                    effective_ctx = auth_manager.pinned_ctx or (
                        ensured_ctx
                        if isinstance(ensured_ctx, SessionContext)
                        else _request_context_snapshot(client)
                    )
                    request_ctx = effective_ctx
                    _request_account(request_ctx)
                    force_recreate = auth_manager.force_recreate_signer
                    auth_manager.force_recreate_signer = False
                    prepared = await _prepare_content_request(
                        client,
                        normalized,
                        auth_manager.current_prepared,
                        force_recreate_signer=force_recreate,
                        ctx=effective_ctx,
                    )
                    request_ctx = prepared.ctx or effective_ctx
                    auth_manager.current_prepared = prepared
                    async with aclosing(
                        client.stream_generate_content(
                            model=normalized.model, prepared=prepared
                        )
                    ) as events:
                        async for event_type, text in _iter_events_with_keepalive(
                            events
                        ):
                            if event_type == "_keepalive":
                                yield ": keepalive\n\n"
                                continue
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
                                if buffered_thought_sig:
                                    # 连续的独立签名按到达顺序输出，不能互相覆盖。
                                    chunk = format_sse_event(
                                        "thought_signature",
                                        buffered_thought_sig,
                                        model_version=normalized.model,
                                        response_id=latest_response_id,
                                    )
                                    if chunk:
                                        has_yielded_data = True
                                        yield chunk
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
                        and not isinstance(
                            exc,
                            (
                                SessionExpiredError,
                                AccountVerificationRequiredError,
                                AccountRestrictedError,
                            ),
                        )
                        and not has_yielded_data
                    ):
                        target_m = normalized.model if normalized else model_path
                        if await auth_manager.handle_403(
                            exc,
                            model=target_m,
                            client=client,
                            request_ctx=request_ctx,
                            has_yielded_data=has_yielded_data,
                        ):
                            continue
                        raise
                    if await handle_attempt_exception(
                        exc,
                        attempt=ordinary_attempt,
                        model_path=model_path,
                        normalized_model=normalized.model if normalized else None,
                        request_ctx=request_ctx,
                        has_yielded_data=has_yielded_data,
                    ):
                        ordinary_attempt += 1
                        auth_manager.current_prepared = None
                        auth_manager.pinned_ctx = None
                        continue
                    raise

            if not stream_completed and last_stream_error is not None:
                raise last_stream_error
            target_model = normalized.model if normalized else model_path
            _record_request_rotator_event(
                "success",
                model=target_model,
                request_ctx=request_ctx,
                usage=final_usage,
            )
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

    return _RequestStreamingResponse(
        stream_with_retries(),
        media_type="text/event-stream",
    )


async def handle_gemini_decisions(
    req: GeminiCreateDecisionRequest,
    client: AIStudioClient,
) -> GeminiCreateDecisionResponse:
    last_error: Exception | None = None
    auth_manager = Auth403RetryManager()
    max_retries = _effective_max_retries()
    total_attempts = max_retries + AUTH_403_MAX_RETRIES
    ordinary_attempt = 0
    raw_model = req.model or "gemini-decision-1"
    norm_model = raw_model if raw_model.startswith("models/") else f"models/{raw_model}"
    questions_dict: dict[str, dict[str, object]] = {
        key: val.model_dump(mode="json", exclude_none=True)
        for key, val in req.questions.items()
    }

    for attempt in range(total_attempts):
        if ordinary_attempt >= max_retries:
            break
        ensured_ctx = await ensure_active_account(attempt, model=norm_model)
        effective_ctx = auth_manager.pinned_ctx or (
            ensured_ctx
            if isinstance(ensured_ctx, SessionContext)
            else _request_context_snapshot(client)
        )
        request_ctx: SessionContext = effective_ctx
        try:
            logger.info(
                "Decisions 请求: model=%s, questions=%d, 尝试=%d/%d",
                norm_model,
                len(questions_dict),
                attempt + 1,
                total_attempts,
            )
            force_recreate = auth_manager.force_recreate_signer
            auth_manager.force_recreate_signer = False
            prepared = auth_manager.current_prepared
            if (
                prepared is not None
                and not force_recreate
                and not _is_prepared_binding_current(prepared)
            ):
                # 与生成接口一致，切号、禁用或凭据更新后不能重放缓存签名。
                prepared = None
            if prepared is None or force_recreate:
                if (
                    isinstance(effective_ctx, SessionContext)
                    and effective_ctx.auth_file
                ):
                    prepared = await client.prepare_decision_request(
                        model=norm_model,
                        raw_input=req.input,
                        questions=questions_dict,
                        temperature=req.temperature,
                        force_recreate_signer=force_recreate,
                        ctx=effective_ctx,
                    )
                else:
                    prepared = await client.prepare_decision_request(
                        model=norm_model,
                        raw_input=req.input,
                        questions=questions_dict,
                        temperature=req.temperature,
                        force_recreate_signer=force_recreate,
                    )
            request_ctx = prepared.ctx or effective_ctx
            auth_manager.current_prepared = prepared
            result = await client.create_decision(
                model=norm_model,
                prepared=prepared,
            )
            auth_manager.current_prepared = None

            raw_usage = result.get("usage")
            usage_map = raw_usage if isinstance(raw_usage, dict) else {}
            prompt_tokens = int(str(usage_map.get("total_input_tokens") or 0))
            total_tokens = int(str(usage_map.get("total_tokens") or prompt_tokens))
            rotator_usage: dict[str, object] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 0,
                "total_tokens": total_tokens,
            }
            _record_request_rotator_event(
                "success",
                model=norm_model,
                request_ctx=request_ctx,
                usage=rotator_usage,
            )
            runtime_state.record(
                norm_model,
                "success",
                usage=rotator_usage,
                finish_reason="STOP",
            )
            raw_answers = result.get("answers")
            answers = raw_answers if isinstance(raw_answers, dict) else {}
            raw_mods = usage_map.get("input_tokens_by_modality")
            modalities = [
                GeminiDecisionModalityUsage(
                    modality=str(item.get("modality") or "text"),
                    tokens=int(str(item.get("tokens") or 0)),
                )
                for item in (raw_mods if isinstance(raw_mods, list) else [])
                if isinstance(item, dict)
            ]
            return GeminiCreateDecisionResponse(
                answers=answers,
                usage=GeminiDecisionUsageResponse(
                    total_input_tokens=prompt_tokens,
                    input_tokens_by_modality=modalities,
                    total_output_tokens=0,
                    total_tokens=total_tokens,
                ),
            )
        except Exception as exc:
            last_error = exc
            if isinstance(exc, AuthError) and not isinstance(
                exc,
                (
                    SessionExpiredError,
                    AccountVerificationRequiredError,
                    AccountRestrictedError,
                ),
            ):
                if await auth_manager.handle_403(
                    exc, model=norm_model, client=client, request_ctx=request_ctx
                ):
                    continue
                raise
            if await handle_attempt_exception(
                exc,
                attempt=ordinary_attempt,
                model_path=norm_model,
                normalized_model=norm_model,
                request_ctx=request_ctx,
            ):
                ordinary_attempt += 1
                auth_manager.current_prepared = None
                auth_manager.pinned_ctx = None
                continue
            raise

    # 普通预算耗尽后保留实际故障，不能将会话失效或传输失败改报日配额。
    error = last_error or TransportError("Request retry budget exhausted.")
    status_code, message, _ = classify_gemini_error_payload(error)
    raise HTTPException(status_code, detail={"message": message}) from error


async def handle_gemini_count_tokens(
    model_path: str,
    req: GeminiCountTokensRequest,
    client: AIStudioClient,
) -> GeminiCountTokensResponse:
    if req.generateContentRequest is not None:
        effective_req = req.generateContentRequest
    elif req.contents is not None:
        effective_req = GeminiGenerateContentRequest(
            contents=req.contents,
            systemInstruction=req.systemInstruction,
            tools=req.tools,
        )
    else:
        raise HTTPException(
            400,
            detail={
                "message": "contents or generateContentRequest is required",
                "type": "bad_request",
            },
        )

    if (
        effective_req.systemInstruction is not None
        and effective_req.systemInstruction.parts
    ) or effective_req.tools:
        # 当前计数 RPC 只承载 contents，拒绝静默丢弃会影响计数的字段。
        raise HTTPException(
            400,
            detail={
                "message": "countTokens currently supports contents only; systemInstruction and tools are not supported.",
                "type": "bad_request",
            },
        )

    normalized = normalize_gemini_request(effective_req, model_path)
    last_error: Exception | None = None
    max_retries = _effective_max_retries()
    for attempt in range(max_retries):
        ensured_ctx = await ensure_active_account(attempt, model=normalized.model)
        request_ctx = (
            ensured_ctx
            if isinstance(ensured_ctx, SessionContext)
            else _request_context_snapshot(client)
        )
        try:
            if isinstance(ensured_ctx, SessionContext):
                res = await client.count_tokens(
                    model=normalized.model,
                    contents=normalized.contents,
                    ctx=ensured_ctx,
                )
            else:
                res = await client.count_tokens(
                    model=normalized.model,
                    contents=normalized.contents,
                )
            raw_cached = res.get("cachedContentTokenCount")
            cached_count = (
                int(str(raw_cached))
                if raw_cached is not None and not isinstance(raw_cached, bool)
                else None
            )
            raw_details = res.get("promptTokensDetails")
            details = (
                [d for d in raw_details if isinstance(d, dict)]
                if isinstance(raw_details, list)
                else None
            )
            return GeminiCountTokensResponse(
                totalTokens=int(str(res.get("totalTokens") or 0)),
                cachedContentTokenCount=cached_count,
                promptTokensDetails=details,
            )
        except Exception as exc:
            last_error = exc
            if await handle_attempt_exception(
                exc,
                attempt=attempt,
                model_path=model_path,
                normalized_model=normalized.model,
                request_ctx=request_ctx,
            ):
                continue
            raise

    error = last_error or TransportError("Request retry budget exhausted.")
    status_code, message, _ = classify_gemini_error_payload(error)
    raise HTTPException(status_code, detail={"message": message}) from error
