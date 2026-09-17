"""Domain errors for AI Studio interactions."""

class AistudioError(Exception):
    pass


class AuthError(AistudioError):
    pass

class SessionExpiredError(AuthError):
    """明确被 Google 重定向至登录页（Cookie 真正失效）。"""


class UsageLimitExceeded(AistudioError):
    pass


class RequestError(AistudioError):
    def __init__(self, status: int, message: str = ""):
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


def classify_error(status: int, body: str) -> AistudioError:
    if status == 429:
        return UsageLimitExceeded(f"配额用完: {body[:200]}")
    if status == 401:
        return AuthError(f"认证失败: {body[:200]}")
    if status == 403:
        return AuthError(f"禁止访问: {body[:200]}")
    return RequestError(status, body[:200])
