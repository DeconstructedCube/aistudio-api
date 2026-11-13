"""Direct HTTP streaming and non-streaming transport layer.

Provides high-performance, browserless HTTP communication with Google AI Studio
using connection pooling and chunk streaming.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator

import httpx

from aistudio_api.config import settings
from aistudio_api.domain.errors import TransportError
from aistudio_api.infrastructure.account.cookie_parser import (
    apply_set_cookies,
    calculate_sapisid_hash,
    filter_minimal_cookies,
    get_cookie_id_from_meta,
    get_tier_header_value,
    load_cookies_from_auth_file,
)
from aistudio_api.infrastructure.gateway.headers import (
    merge_header_case_insensitive,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("transport")

# 这些头由本层重新计算或由 httpx 自行填充，必须整条剔除后再重建。
_DROPPED_HEADER_NAMES = frozenset(
    {
        "host",
        "content-length",
        "authorization",
        "x-goog-authuser",
        "x-aistudio-g1-tier",
    }
)


def _get_auth_cookies(auth_file: str | None = None) -> dict[str, str] | None:
    """Read credentials from the bound auth_file or active account."""
    if auth_file:
        cookies = load_cookies_from_auth_file(auth_file)
        if cookies:
            return cookies
    try:
        from aistudio_api.infrastructure.account.account_store import AccountStore

        store = AccountStore()
        active = store.get_active_account()
        if not active:
            return None
        auth_path = store.get_auth_path_optional(active.id, require_exists=True)
        if auth_path:
            cookies = load_cookies_from_auth_file(auth_path)
            if cookies:
                return cookies
    except Exception:
        pass
    return None


def _get_active_account_tier() -> str:
    """Read active account's subscription tier."""
    try:
        from aistudio_api.infrastructure.account.account_store import AccountStore

        store = AccountStore()
        active = store.get_active_account()
        if active and getattr(active, "tier", None):
            return str(active.tier)
    except Exception:
        pass
    return "Free"


def _sync_response_cookies(
    resp: httpx.Response,
    auth_file: str | None = None,
    cookies: dict[str, str] | None = None,
    expected_cookie_id: str | None = None,
) -> None:
    """捕获上游响应中的 Set-Cookie 并同步回内存与 AccountStore。"""
    set_cookies = resp.headers.get_list("set-cookie")
    if not set_cookies:
        return
    target_map = (
        cookies if cookies is not None else (_get_auth_cookies(auth_file) or {})
    )
    if not target_map:
        return
    updated = apply_set_cookies(target_map, set_cookies)
    if not updated:
        return
    logger.debug("捕获上游 Set-Cookie 更新: %s", updated)
    try:
        from aistudio_api.infrastructure.account.account_store import AccountStore

        AccountStore().update_account_cookies(
            auth_file=auth_file,
            new_cookies=target_map,
            expected_cookie_id=expected_cookie_id,
        )
    except Exception as e:
        logger.debug("持久化 Set-Cookie 失败: %s", e)


async def _ensure_authorization_header(
    headers: dict[str, str],
    auth_user: str = "0",
    tier: str | None = None,
    auth_file: str | None = None,
    cookies: dict[str, str] | None = None,
) -> dict[str, str]:
    """Ensure SAPISIDHASH Authorization, X-Goog-AuthUser, and X-AIStudio-G1-Tier are set."""
    incoming_auth = ""
    for k, v in headers.items():
        if k.lower() == "authorization" and v:
            incoming_auth = str(v)
            break

    # 大小写不敏感累积：HTTP/2 会把同名头压成同一个名字，若字典里残留仅大小写不同的
    # 两个键（如 -bin 与 -Bin），httpx 会把两条都发出去并触发上游 403。
    clean_headers: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _DROPPED_HEADER_NAMES:
            continue
        merge_header_case_insensitive(clean_headers, str(k), str(v))
    merge_header_case_insensitive(
        clean_headers, "X-Goog-AuthUser", str(auth_user or "0")
    )
    effective_tier = tier if tier is not None else _get_active_account_tier()
    tier_header = get_tier_header_value(effective_tier)
    if tier_header:
        merge_header_case_insensitive(clean_headers, "X-AIStudio-G1-Tier", tier_header)

    raw_cookie_dict = cookies or _get_auth_cookies(auth_file)
    cookie_dict = filter_minimal_cookies(raw_cookie_dict) if raw_cookie_dict else None

    if cookie_dict:
        fresh_auth = calculate_sapisid_hash(cookie_dict)
        if fresh_auth:
            merge_header_case_insensitive(clean_headers, "Authorization", fresh_auth)
        elif incoming_auth:
            merge_header_case_insensitive(clean_headers, "Authorization", incoming_auth)
        merge_header_case_insensitive(
            clean_headers,
            "Cookie",
            "; ".join(f"{k}={v}" for k, v in cookie_dict.items() if v),
        )
    elif incoming_auth:
        merge_header_case_insensitive(clean_headers, "Authorization", incoming_auth)

    return clean_headers


class HTTPStreamTransport:
    """Direct HTTP streaming and non-streaming transport layer."""

    def __init__(self, proxy_url: str | None = None, timeout: float = 900.0) -> None:
        self.proxy_url = proxy_url or settings.proxy_url
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=None,
                    connect=20.0,
                    read=self.timeout,
                    write=60.0,
                    pool=20.0,
                ),
                proxy=self.proxy_url,
                http2=True,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._client

    async def send_request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
        auth_user: str = "0",
        tier: str | None = None,
        auth_file: str | None = None,
        cookies: dict[str, str] | None = None,
        expected_cookie_id: str | None = None,
    ) -> tuple[int, bytes]:
        """Execute non-streaming HTTP request directly."""
        t0 = time.perf_counter()
        # 必须在网络等待前捕获 Cookie 与代际，不能用响应到达后的新凭据回填。
        request_cookies = (
            cookies if cookies is not None else (_get_auth_cookies(auth_file) or {})
        )
        cookie_id = (
            expected_cookie_id
            if expected_cookie_id is not None
            else get_cookie_id_from_meta(auth_file)
        )
        clean_headers = await _ensure_authorization_header(
            headers,
            auth_user=auth_user,
            tier=tier,
            auth_file=auth_file,
            cookies=request_cookies,
        )
        logger.debug(
            "发送请求: url=%s, body_len=%d, u/%s",
            url,
            len(body),
            auth_user,
        )

        client = await self._get_client()
        try:
            resp = await client.post(
                url, headers=clean_headers, content=body.encode("utf-8")
            )
            _sync_response_cookies(
                resp,
                auth_file=auth_file,
                cookies=request_cookies,
                expected_cookie_id=cookie_id,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.debug(
                "请求完成: status=%d, 耗时=%.1fms, 响应长度=%d",
                resp.status_code,
                elapsed_ms,
                len(resp.content),
            )
            return resp.status_code, resp.content
        except Exception as exc:
            err_detail = (
                f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            )
            logger.error("HTTP 请求失败: url=%s, error=%s", url, err_detail)
            raise TransportError(f"HTTP request failed: {err_detail}") from exc

    async def send_streaming_request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
        auth_user: str = "0",
        tier: str | None = None,
        auth_file: str | None = None,
        cookies: dict[str, str] | None = None,
        expected_cookie_id: str | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Stream HTTP response chunks directly."""
        # 必须在网络等待前捕获 Cookie 与代际，不能用响应到达后的新凭据回填。
        request_cookies = (
            cookies if cookies is not None else (_get_auth_cookies(auth_file) or {})
        )
        cookie_id = (
            expected_cookie_id
            if expected_cookie_id is not None
            else get_cookie_id_from_meta(auth_file)
        )
        clean_headers = await _ensure_authorization_header(
            headers,
            auth_user=auth_user,
            tier=tier,
            auth_file=auth_file,
            cookies=request_cookies,
        )
        logger.debug("建立流式请求: url=%s, u/%s", url, auth_user)

        client = await self._get_client()

        resp = None
        try:
            req = client.build_request(
                "POST", url, headers=clean_headers, content=body.encode("utf-8")
            )
            resp = await client.send(req, stream=True)
            _sync_response_cookies(
                resp,
                auth_file=auth_file,
                cookies=request_cookies,
                expected_cookie_id=cookie_id,
            )
            yield ("status", resp.status_code)

            if resp.status_code != 200:
                err_body = await resp.aread()
                yield ("chunk", err_body)
                return

            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield ("chunk", chunk)

        except Exception as exc:
            err_detail = (
                f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            )
            logger.warning("流式传输异常: %s", err_detail)
            yield ("error", err_detail)
        finally:
            if resp is not None:
                await resp.aclose()
