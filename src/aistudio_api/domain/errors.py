"""Domain errors for AI Studio interactions."""

from __future__ import annotations

import json


class AistudioError(Exception):
    pass


class AuthError(AistudioError):
    pass


class SessionExpiredError(AuthError):
    """明确被 Google 重定向至登录页（Cookie 真正失效）。"""


class StaleSessionError(AistudioError):
    """请求持有的凭据代际已被重新导入替换，重试时不得惩罚当前账号。"""


class UsageLimitExceeded(AistudioError):
    pass


class RequestError(AistudioError):
    def __init__(self, status: int, message: str = ""):
        self.status = status
        super().__init__(f"HTTP {status}: {message}" if message else f"HTTP {status}")


class TransportError(RuntimeError):
    """Upstream HTTP or WAA proof transport failure before completion."""


GRPC_CODE_TO_HTTP: dict[int, int] = {
    1: 499,  # CANCELLED
    2: 500,  # UNKNOWN
    3: 400,  # INVALID_ARGUMENT
    4: 504,  # DEADLINE_EXCEEDED
    5: 404,  # NOT_FOUND
    6: 409,  # ALREADY_EXISTS
    7: 403,  # PERMISSION_DENIED
    8: 429,  # RESOURCE_EXHAUSTED
    9: 400,  # FAILED_PRECONDITION
    10: 409,  # ABORTED
    11: 400,  # OUT_OF_RANGE
    12: 501,  # UNIMPLEMENTED
    13: 500,  # INTERNAL
    14: 503,  # UNAVAILABLE
    15: 500,  # DATA_LOSS
    16: 401,  # UNAUTHENTICATED
}


def parse_upstream_grpc_error(raw_response: str) -> tuple[int | None, str]:
    """Parse Google JSPB/JSON error structures into (grpc_code, message)."""
    if not raw_response:
        return None, ""
    text = raw_response.strip()

    normalized = text
    if normalized.startswith("[,"):
        normalized = "[null," + normalized[2:]

    try:
        data = json.loads(normalized)
        if isinstance(data, list):

            def _find_grpc(obj: object) -> tuple[int | None, str] | None:
                if isinstance(obj, list):
                    if (
                        len(obj) >= 2
                        and isinstance(obj[0], int)
                        and isinstance(obj[1], str)
                        and obj[1]
                    ):
                        return obj[0], obj[1]
                    for item in obj:
                        res = _find_grpc(item)
                        if res is not None:
                            return res
                elif isinstance(obj, dict):
                    err = obj.get("error")
                    if isinstance(err, dict) and "message" in err:
                        code = err.get("code")
                        return (
                            int(code) if isinstance(code, (int, float)) else None
                        ), str(err["message"])
                return None

            found = _find_grpc(data)
            if found is not None:
                return found
    except Exception:
        pass

    return None, text


def is_html_payload(text: str, content_type: str = "") -> bool:
    """检测上游响应是否为 HTML 页面（边缘风控拦截页或登录跳转页）。"""
    ct_lower = content_type.lower()
    if "text/html" in ct_lower or "application/xhtml" in ct_lower:
        return True
    head = text.lstrip()[:120].lower()
    return head.startswith(("<!doctype html", "<html"))


def is_login_redirect_payload(text: str) -> bool:
    """检测响应文本是否包含 Google 登录页或欢迎页重定向特征。"""
    lower = text[:2000].lower()
    return (
        "servicelogin" in lower
        or "accounts.google.com/v3/signin" in lower
        or "aistudio.google.com/welcome" in lower
    )


def classify_error(
    status: int, body: str, content_type: str = ""
) -> AistudioError | TransportError:
    if is_login_redirect_payload(body):
        return SessionExpiredError("登录态已失效: 被重定向至 Google 登录页")
    grpc_code, msg = parse_upstream_grpc_error(body)
    clean_msg = msg or body[:200]
    if grpc_code == 8 or status == 429:
        return UsageLimitExceeded(f"配额耗尽: {clean_msg}")
    if grpc_code == 16 or status == 401:
        return SessionExpiredError(f"认证失效 (HTTP 401): {clean_msg}")
    if grpc_code == 7 or status == 403:
        return AuthError(f"权限被拒绝 (HTTP 403): {clean_msg}")
    if is_html_payload(body, content_type):
        return TransportError(f"上游返回 HTML 错误页 (HTTP {status})")
    effective_status = GRPC_CODE_TO_HTTP.get(grpc_code, status) if grpc_code else status
    return RequestError(effective_status, clean_msg)


def is_transient_rpc_error(exc: Exception) -> bool:
    """识别 Google 上游偶发且完全随机的 RPC 路由 404 (Ambiguous request for service)。"""
    if isinstance(exc, RequestError) and exc.status == 404:
        err = str(exc).lower()
        return "ambiguous request for service" in err and "generatecontent" in err
    return False


def is_transport_error(exc: Exception) -> bool:
    """识别由于传输中断、签名超时或连接异常引起的网关传输故障。"""
    if isinstance(exc, TransportError):
        return True
    if isinstance(exc, (RuntimeError, TimeoutError)):
        err = str(exc).lower()
        return any(
            k in err
            for k in (
                "timeout",
                "closed",
                "aborted",
                "snapshot",
                "connection",
            )
        )
    return False
