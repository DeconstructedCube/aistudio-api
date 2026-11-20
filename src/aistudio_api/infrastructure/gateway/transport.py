"""Direct HTTP streaming and non-streaming transport layer.

Provides high-performance, browserless HTTP communication with Google AI Studio
using connection pooling and chunk streaming.
"""

from __future__ import annotations

import contextlib
import socket
import struct
import time
from collections.abc import AsyncGenerator

import httpcore
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

# 上游长思考/长工具调用无数据时，每 25s 发送一次端到端 HTTP/2 PING 帧（穿透代理与 NAT），
# 防止中间代理或 Google GFE 在 60s~120s 静默期强制断连导致 502。
H2_PING_INTERVAL_SECONDS = 25.0
_h2_ping_installed = False


def _build_keepalive_socket_options() -> list[tuple[int, int, int]]:
    """Build portable TCP SO_KEEPALIVE socket options for long-lived streams."""
    opts: list[tuple[int, int, int]] = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    keepidle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(
        socket, "TCP_KEEPALIVE", None
    )
    if isinstance(keepidle, int):
        opts.append((socket.IPPROTO_TCP, keepidle, 30))
    keepintvl = getattr(socket, "TCP_KEEPINTVL", None)
    if isinstance(keepintvl, int):
        opts.append((socket.IPPROTO_TCP, keepintvl, 10))
    keepcnt = getattr(socket, "TCP_KEEPCNT", None)
    if isinstance(keepcnt, int):
        opts.append((socket.IPPROTO_TCP, keepcnt, 6))
    return opts


def _install_http2_ping_keepalive() -> None:
    """Install RFC 9113 HTTP/2 PING keepalive into httpcore.AsyncHTTP2Connection.

    httpcore uses hyper-h2 but never emits HTTP/2 PING frames during long silent
    reads (such as deep thinking or large buffered tool calls >120s), causing
    proxies, NATs, and Google GFE to drop idle tunnels with RemoteProtocolError.
    Slicing network reads at H2_PING_INTERVAL_SECONDS and sending a PING frame
    through the TLS stream under `_write_lock` resets application-layer idle
    timers end-to-end without background tasks or thread-safety hazards.
    """
    global _h2_ping_installed
    if _h2_ping_installed:
        return
    try:
        from httpcore._async import http2 as hc_http2
    except Exception:
        return

    conn_cls = getattr(hc_http2, "AsyncHTTP2Connection", None)
    if conn_cls is None or getattr(conn_cls, "_aistudio_h2_ping_installed", False):
        _h2_ping_installed = True
        return

    async def _read_incoming_data_with_ping(
        self: object, request: httpcore.Request
    ) -> list[object]:
        timeouts = request.extensions.get("timeout", {})
        total_timeout = timeouts.get("read", None)
        write_timeout = timeouts.get("write", 30.0)

        read_exc = getattr(self, "_read_exception", None)
        if read_exc is not None:
            raise read_exc

        deadline = (
            time.monotonic() + float(total_timeout)
            if total_timeout is not None
            else None
        )
        ping_seq = int(getattr(self, "_aistudio_ping_seq", 0))

        try:
            while True:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise httpcore.ReadTimeout("Timed out reading HTTP/2 stream")
                    slice_timeout: float | None = min(
                        remaining, H2_PING_INTERVAL_SECONDS
                    )
                else:
                    slice_timeout = H2_PING_INTERVAL_SECONDS

                try:
                    data = await self._network_stream.read(  # type: ignore[attr-defined]
                        self.READ_NUM_BYTES,  # type: ignore[attr-defined]
                        slice_timeout,
                    )
                except httpcore.ReadTimeout:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise
                    if (
                        getattr(self, "_connection_terminated", None) is not None
                        or getattr(self, "_write_exception", None) is not None
                    ):
                        raise
                    ping_seq += 1
                    self._aistudio_ping_seq = ping_seq  # type: ignore[attr-defined]
                    opaque = struct.pack("!Q", ping_seq & 0xFFFFFFFFFFFFFFFF)
                    async with self._write_lock:  # type: ignore[attr-defined]
                        self._h2_state.ping(opaque)  # type: ignore[attr-defined]
                        outgoing = self._h2_state.data_to_send()  # type: ignore[attr-defined]
                        if outgoing:
                            await self._network_stream.write(  # type: ignore[attr-defined]
                                outgoing, write_timeout
                            )
                    continue

                if data == b"":
                    raise httpcore.RemoteProtocolError("Server disconnected")
                break
        except Exception as exc:
            self._read_exception = exc  # type: ignore[attr-defined]
            self._connection_error = True  # type: ignore[attr-defined]
            raise exc

        return list(self._h2_state.receive_data(data))  # type: ignore[attr-defined]

    conn_cls._read_incoming_data = _read_incoming_data_with_ping  # type: ignore[method-assign]
    conn_cls._aistudio_h2_ping_installed = True
    _h2_ping_installed = True


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
        self._inflight: dict[int, int] = {}
        self._stale_clients: dict[int, httpx.AsyncClient] = {}

    async def close(self) -> None:
        client = self._client
        self._client = None
        stale = list(self._stale_clients.values())
        self._stale_clients.clear()
        self._inflight.clear()
        if client is not None and not client.is_closed:
            await client.aclose()
        for old in stale:
            if old is not client and not old.is_closed:
                with contextlib.suppress(Exception):
                    await old.aclose()

    def _acquire_inflight(self, client: httpx.AsyncClient) -> None:
        cid = id(client)
        self._inflight[cid] = self._inflight.get(cid, 0) + 1

    async def _release_inflight(self, client: httpx.AsyncClient) -> None:
        cid = id(client)
        remaining = self._inflight.get(cid, 1) - 1
        if remaining <= 0:
            self._inflight.pop(cid, None)
            stale = self._stale_clients.pop(cid, None)
            if stale is not None and not stale.is_closed:
                with contextlib.suppress(Exception):
                    await stale.aclose()
        else:
            self._inflight[cid] = remaining

    async def _reset_stale_client(self, client: httpx.AsyncClient) -> None:
        """Detach a real pooled transport client after connection-level failure; close once in-flight requests drain."""
        transport = getattr(client, "_transport", None)
        if not isinstance(transport, httpx.AsyncHTTPTransport):
            return
        if self._client is client:
            self._client = None
        cid = id(client)
        if self._inflight.get(cid, 0) > 1:
            self._stale_clients[cid] = client
            return
        self._stale_clients.pop(cid, None)
        if not client.is_closed:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            _install_http2_ping_keepalive()
            transport = httpx.AsyncHTTPTransport(
                proxy=self.proxy_url,
                http2=True,
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50,
                    keepalive_expiry=60.0,
                ),
                socket_options=_build_keepalive_socket_options(),
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=None,
                    connect=20.0,
                    read=self.timeout,
                    write=60.0,
                    pool=20.0,
                ),
                transport=transport,
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
        self._acquire_inflight(client)
        try:
            resp = await client.post(
                url,
                headers=clean_headers,
                content=body.encode("utf-8", errors="replace"),
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
            if isinstance(
                exc,
                (
                    httpx.ConnectTimeout,
                    httpx.ConnectError,
                    httpx.PoolTimeout,
                    httpx.RemoteProtocolError,
                ),
            ):
                await self._reset_stale_client(client)
            logger.error(
                "HTTP 请求失败: url=%s, proxy=%s, error=%s",
                url,
                self.proxy_url or "none",
                err_detail,
            )
            raise TransportError(f"HTTP request failed: {err_detail}") from exc
        finally:
            await self._release_inflight(client)

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
        self._acquire_inflight(client)

        resp = None
        try:
            req = client.build_request(
                "POST",
                url,
                headers=clean_headers,
                content=body.encode("utf-8", errors="replace"),
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
            if isinstance(
                exc,
                (
                    httpx.ConnectTimeout,
                    httpx.ConnectError,
                    httpx.PoolTimeout,
                    httpx.RemoteProtocolError,
                ),
            ):
                await self._reset_stale_client(client)
            logger.warning(
                "流式传输异常 (proxy=%s): %s",
                self.proxy_url or "none",
                err_detail,
            )
            yield ("error", err_detail)
        finally:
            try:
                if resp is not None:
                    await resp.aclose()
            finally:
                await self._release_inflight(client)
