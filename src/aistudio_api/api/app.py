"""FastAPI application entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aistudio_api.application.api_service_gemini import classify_gemini_error_payload
from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_logging_settings,
)
from aistudio_api.infrastructure.utils.logger import (
    dump_request_exchange,
    get_current_request_account,
    get_log_level,
    get_logger,
    is_dump_requests_enabled,
    is_dump_to_file_enabled,
    set_current_request_account,
    setup_logging,
)

from .dependencies import require_api_key, require_web_auth
from .routes_accounts import router as accounts_router
from .routes_gemini import router as gemini_router
from .routes_models import router as models_router
from .routes_system import (
    protected_router as system_protected_router,
    public_router as system_public_router,
)
from .state import runtime_state

_log_cfg = get_configured_logging_settings()
setup_logging(str(_log_cfg.get("level", "INFO")))
logger = get_logger("server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from aistudio_api.infrastructure.waa.daemon import (
        ensure_signer_ready,
        stop_signer_daemon,
    )

    # 启动时预热本地 WAA IPC 签名子进程
    await ensure_signer_ready()
    from aistudio_api.application.account_rotator import init_rotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_store import AccountStore

    client = AIStudioClient()
    runtime_state.client = client

    # 初始化账号管理服务
    account_store = AccountStore()
    account_service = AccountService(account_store)
    runtime_state.account_service = account_service

    # 初始化黏性账号调度器
    rotator = init_rotator(account_store)
    runtime_state.rotator = rotator

    active_auth_path = account_store.get_active_auth_path()
    if active_auth_path is not None:
        await client.switch_auth(str(active_auth_path))
    logger.info(
        "服务启动完成: port=%d, 账号数=%d",
        settings.port,
        len(account_store.list_accounts()),
    )
    logger.debug(
        "账号列表: %s",
        [
            f"{a.id}(u/{a.auth_user},tier={a.tier},enabled={a.enabled})"
            for a in account_store.list_accounts()
        ],
    )

    async def _auto_refresh_tiers() -> None:
        import httpx

        from aistudio_api.infrastructure.account.cookie_parser import (
            load_cookies_from_auth_file,
            probe_account_benefit_tier,
            probe_account_email,
        )

        now_ts = time.time()
        accounts_to_check = [
            a
            for a in account_store.list_accounts()
            if not a.email
            or (a.tier_checked_at is None)
            or (now_ts - (a.tier_checked_at or 0) > 86400 * 3)
        ]
        if not accounts_to_check:
            return

        logger.info("校验账号邮箱与订阅等级: 待检测=%d", len(accounts_to_check))
        upgraded_count = 0
        checked_count = 0
        sem = asyncio.Semaphore(4)

        async with httpx.AsyncClient(
            timeout=8.0, proxy=settings.proxy_url, follow_redirects=True
        ) as probe_client:

            async def _check_one(a):
                nonlocal checked_count, upgraded_count
                apath = account_store.get_auth_path(a.id)
                if not apath:
                    return
                async with sem:
                    try:
                        cdict = load_cookies_from_auth_file(apath)
                        if not cdict:
                            return
                        if not a.email:
                            em = await probe_account_email(
                                cdict,
                                auth_user=a.auth_user or "0",
                                client=probe_client,
                            )
                            if em:
                                account_store.update_account_email(a.id, em)
                        old_tier = a.tier or "Free"
                        probed_tier = await probe_account_benefit_tier(
                            cdict,
                            auth_user=a.auth_user or "0",
                            client=probe_client,
                            default=None,
                        )
                        if probed_tier is None:
                            logger.debug(
                                "账号 %s (u/%s) 订阅等级探测未成功，保留原等级 %s",
                                a.id,
                                a.auth_user,
                                old_tier,
                            )
                            return
                        checked_count += 1
                        resolved_tier = (
                            "Ultra_20X"
                            if old_tier == "Ultra_20X" and probed_tier == "Ultra"
                            else probed_tier
                        )
                        account_store.update_account_tier(
                            a.id, resolved_tier, checked_at=now_ts
                        )
                        active_now = account_store.get_active_account()
                        if (
                            active_now is not None
                            and active_now.id == a.id
                            and client.session is not None
                            and client.session.active_context is not None
                        ):
                            client.session.active_context.tier = resolved_tier
                        if resolved_tier != old_tier:
                            if old_tier == "Free" and resolved_tier != "Free":
                                upgraded_count += 1
                            from aistudio_api.infrastructure.gateway.model_discovery import (
                                model_discovery,
                            )

                            model_discovery.invalidate_cache()
                            logger.info(
                                "账号订阅等级更新: id=%s (u/%s) %s -> %s",
                                a.id,
                                a.auth_user,
                                old_tier,
                                resolved_tier,
                            )
                    except Exception as e:
                        logger.debug("账号 %s 订阅检测失败: %s", a.id, e)

            await asyncio.gather(*(_check_one(a) for a in accounts_to_check))

        logger.info(
            "账号校验完成: 已检测=%d, 付费升级=%d",
            checked_count,
            upgraded_count,
        )

    async def _cookie_rotation_loop() -> None:
        """后台定时轮换账号滚动 Cookie (__Secure-1PSIDTS / SIDCC)，间隔 7 分钟。"""
        import httpx

        from aistudio_api.infrastructure.account.cookie_parser import (
            compute_session_anchor,
            load_cookies_from_auth_file,
            probe_aistudio_session,
        )

        await asyncio.sleep(5.0)
        while True:
            try:
                now_ts = time.time()
                seen_anchors: set[tuple[str, str]] = set()
                accounts = account_store.list_accounts()
                async with httpx.AsyncClient(
                    timeout=15.0, proxy=settings.proxy_url, follow_redirects=False
                ) as rot_client:
                    for acc in accounts:
                        if not acc.enabled:
                            continue
                        st = rotator.get_account_stats(acc.id)
                        if st and st.session_expired:
                            continue
                        last_rot = acc.last_rotated_at or 0.0
                        if last_rot and (now_ts - last_rot < 420.0):
                            continue
                        apath = account_store.get_auth_path(acc.id)
                        if not apath:
                            continue
                        cdict = load_cookies_from_auth_file(apath)
                        if not cdict:
                            continue
                        # 同源 Cookie 不同导入代际分别轮换，且在异步操作前保存代际。
                        cookie_id = acc.cookie_id or ""
                        anchor = compute_session_anchor(cdict)
                        if anchor:
                            session_key = (anchor, cookie_id)
                            if session_key in seen_anchors:
                                continue
                            seen_anchors.add(session_key)
                        before_ts = cdict.get("__Secure-1PSIDTS", "")
                        rot_res = await account_service.rotate_account_cookies(
                            acc.id, client.session, client=rot_client
                        )
                        if rot_res.get(
                            "stale"
                        ) is True or not account_service.is_cookie_generation_current(
                            acc.id, cookie_id
                        ):
                            continue
                        status_code = rot_res.get("status", 0)
                        if status_code == 401:
                            alive, _ = await probe_aistudio_session(
                                cdict, client=rot_client
                            )
                            if not account_service.is_cookie_generation_current(
                                acc.id, cookie_id
                            ):
                                continue
                            if not alive:
                                rotator.record_session_expired(acc.id)
                            continue
                        new_cdict = rot_res.get("cookies")
                        after_ts = (
                            new_cdict.get("__Secure-1PSIDTS", "")
                            if isinstance(new_cdict, dict)
                            else ""
                        )
                        if (
                            status_code == 200
                            and before_ts
                            and after_ts
                            and before_ts == after_ts
                        ):
                            alive, _ = await probe_aistudio_session(
                                new_cdict if isinstance(new_cdict, dict) else cdict,
                                client=rot_client,
                            )
                            if not account_service.is_cookie_generation_current(
                                acc.id, cookie_id
                            ):
                                continue
                            if not alive:
                                rotator.record_session_expired(acc.id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("后台 Cookie 轮换巡检异常: %s", e)
            await asyncio.sleep(60.0)

    tier_task = asyncio.create_task(_auto_refresh_tiers())
    rotate_task = asyncio.create_task(_cookie_rotation_loop())

    yield
    logger.info("正在关闭服务")
    bg_tasks = [t for t in (tier_task, rotate_task) if t and not t.done()]
    for bg_task in bg_tasks:
        bg_task.cancel()
    if bg_tasks:
        await asyncio.gather(*bg_tasks, return_exceptions=True)
    for pending_save in (
        getattr(runtime_state, "_save_task", None),
        getattr(rotator, "_save_task", None),
    ):
        done_fn = getattr(pending_save, "done", None)
        cancel_fn = getattr(pending_save, "cancel", None)
        if callable(done_fn) and callable(cancel_fn) and not done_fn():
            cancel_fn()
            await asyncio.gather(pending_save, return_exceptions=True)  # type: ignore[arg-type]
    runtime_state.save_stats()
    if rotator is not None and callable(getattr(rotator, "save_state", None)):
        rotator.save_state()
    if client:
        try:
            await client.close()
        except Exception as e:
            logger.debug("关闭客户端异常: %s", e)
    await stop_signer_daemon()
    runtime_state.client = None
    runtime_state.account_service = None
    runtime_state.rotator = None


app = FastAPI(title="AI Studio API", lifespan=lifespan)
app.include_router(system_public_router)
app.include_router(system_protected_router, dependencies=[Depends(require_web_auth)])
app.include_router(
    accounts_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(accounts_router, dependencies=[Depends(require_web_auth)])
app.include_router(gemini_router, dependencies=[Depends(require_api_key)])
app.include_router(models_router, dependencies=[Depends(require_api_key)])


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    code, msg, status_str = classify_gemini_error_payload(exc)
    if code >= 500:
        logger.error(
            "HTTP %d 异常: path=%s, message=%s",
            code,
            _request.url.path,
            msg,
        )
    elif code in (401, 403, 429):
        logger.warning(
            "HTTP %d: path=%s, message=%s",
            code,
            _request.url.path,
            msg,
        )
    else:
        logger.info(
            "HTTP %d: path=%s, message=%s",
            code,
            _request.url.path,
            msg,
        )
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "code": code,
                "message": msg,
                "status": status_str,
            }
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    code, msg, status_str = 400, str(exc), "INVALID_ARGUMENT"
    logger.warning("请求参数校验失败: path=%s, error=%s", _request.url.path, exc)
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "code": code,
                "message": msg,
                "status": status_str,
            }
        },
    )


def _safe_log_path(path: str) -> str:
    if path.startswith("/api-keys/") and len(path) > len("/api-keys/") + 8:
        raw_k = path[len("/api-keys/") :]
        return f"/api-keys/{raw_k[:8]}..."
    return path


@app.middleware("http")
async def logging_and_dump_middleware(request, call_next):
    """统一请求生命周期日志与请求转储 (DEBUG) 中间件。"""

    start_time = time.perf_counter()
    path = request.url.path
    log_path = _safe_log_path(path)
    is_static = path.startswith(("/static", "/assets")) or path in ("/favicon.ico",)
    console_dump = is_dump_requests_enabled() and not is_static
    file_dump = is_dump_to_file_enabled() and not is_static
    dump_enabled = console_dump or file_dump

    req_id = ""
    body_text = None
    client_str = ""
    headers_dict = None
    query_dict = None
    req_model: str | None = None
    if path.startswith("/v1beta/models/"):
        req_model = path.removeprefix("/v1beta/models/").split(":", 1)[0] or None
    elif path == "/v1beta/decisions":
        req_model = "gemini-decision-1"

    if dump_enabled:
        req_id = f"req_{secrets.token_hex(4)}"
        client_str = (
            f"{request.client.host}:{request.client.port}"
            if request.client
            else "unknown"
        )
        headers_dict = dict(request.headers)
        query_dict = dict(request.query_params)
        try:
            body_bytes = await request.body()
            body_text = body_bytes.decode("utf-8", errors="replace")
        except Exception:
            body_text = "(failed to read request body)"

    set_current_request_account(None, None)
    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        ctx_acc_id, ctx_auth_user = get_current_request_account()
        active_acc = (
            runtime_state.account_service.get_active_account()
            if runtime_state.account_service
            else None
        )
        if dump_enabled:
            dump_request_exchange(
                req_id=req_id,
                method=request.method,
                url=str(request.url),
                client=client_str,
                headers=headers_dict or {},
                query_params=query_dict or {},
                body_text=body_text,
                status_code=500,
                elapsed_ms=elapsed_ms,
                response_text=f"Internal Server Error (Exception: {exc})",
                model=req_model,
                account_id=ctx_acc_id or (active_acc.id if active_acc else None),
                auth_user=ctx_auth_user
                or (active_acc.auth_user if active_acc else None),
                error_detail=str(exc),
                emit_console=console_dump,
            )
        else:
            logger.error(
                "HTTP %s %s -> 500 (%.1fms, %s)",
                request.method,
                log_path,
                elapsed_ms,
                exc,
            )
        raise exc

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    content_type = response.headers.get("content-type", "")
    is_event_stream = "text/event-stream" in content_type
    is_streaming_resp = (
        is_event_stream or response.headers.get("x-accel-buffering", "").lower() == "no"
    )

    if dump_enabled:
        ctx_acc_id, ctx_auth_user = get_current_request_account()
        active_acc = (
            runtime_state.account_service.get_active_account()
            if runtime_state.account_service
            else None
        )
        req_acc_id = ctx_acc_id or (active_acc.id if active_acc else None)
        req_auth_user = ctx_auth_user or (active_acc.auth_user if active_acc else None)
        if is_streaming_resp:
            orig_iterator = response.body_iterator
            captured_chunks: list[bytes] = []
            captured_size = 0
            max_capture_bytes = 512 * 1024

            async def _tee_stream():
                nonlocal captured_size
                stream_err: str | None = None
                try:
                    async for chunk in orig_iterator:
                        raw_chunk = (
                            chunk
                            if isinstance(chunk, bytes)
                            else str(chunk).encode("utf-8")
                        )
                        if captured_size < max_capture_bytes:
                            remaining = max_capture_bytes - captured_size
                            captured_chunks.append(raw_chunk[:remaining])
                            captured_size += len(raw_chunk[:remaining])
                        yield chunk
                except Exception as stream_exc:
                    stream_err = str(stream_exc)
                    raise
                finally:
                    total_ms = (time.perf_counter() - start_time) * 1000
                    resp_text = b"".join(captured_chunks).decode(
                        "utf-8", errors="replace"
                    )
                    s_acc_id, s_auth_user = get_current_request_account()
                    dump_request_exchange(
                        req_id=req_id,
                        method=request.method,
                        url=str(request.url),
                        client=client_str,
                        headers=headers_dict or {},
                        query_params=query_dict or {},
                        body_text=body_text,
                        status_code=response.status_code,
                        elapsed_ms=total_ms,
                        response_headers=dict(response.headers),
                        response_text=resp_text or "(empty stream)",
                        is_stream=is_event_stream,
                        model=req_model,
                        account_id=s_acc_id or req_acc_id,
                        auth_user=s_auth_user or req_auth_user,
                        error_detail=stream_err,
                        emit_console=console_dump,
                    )

            response.body_iterator = _tee_stream()
            if not console_dump:
                logger.info(
                    "HTTP %s %s -> %d (%.1fms)",
                    request.method,
                    log_path,
                    response.status_code,
                    elapsed_ms,
                )
        else:
            max_capture_bytes = 512 * 1024
            try:
                # 读取非流式响应体并重构 iterator 以便正常返回给客户端（转储截取上限 512KB）
                chunks = [chunk async for chunk in response.body_iterator]
                captured_parts: list[bytes] = []
                captured_len = 0
                total_len = 0
                for c in chunks:
                    raw_c = c if isinstance(c, bytes) else str(c).encode("utf-8")
                    total_len += len(raw_c)
                    if captured_len < max_capture_bytes:
                        rem = max_capture_bytes - captured_len
                        captured_parts.append(raw_c[:rem])
                        captured_len += len(raw_c[:rem])
                resp_text = b"".join(captured_parts).decode("utf-8", errors="replace")
                if total_len > max_capture_bytes:
                    resp_text += f"\n... [truncated dump at {max_capture_bytes} of {total_len} bytes]"

                async def _replay_iterator():
                    for c in chunks:
                        yield c

                response.body_iterator = _replay_iterator()
            except Exception:
                resp_text = "(failed to read response body)"

            dump_request_exchange(
                req_id=req_id,
                method=request.method,
                url=str(request.url),
                client=client_str,
                headers=headers_dict or {},
                query_params=query_dict or {},
                body_text=body_text,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
                response_headers=dict(response.headers),
                response_text=resp_text,
                is_stream=False,
                model=req_model,
                account_id=req_acc_id,
                auth_user=req_auth_user,
                emit_console=console_dump,
            )
            if not console_dump:
                logger.info(
                    "HTTP %s %s -> %d (%.1fms)",
                    request.method,
                    log_path,
                    response.status_code,
                    elapsed_ms,
                )
    elif not is_static:
        logger.info(
            "HTTP %s %s -> %d (%.1fms)",
            request.method,
            log_path,
            response.status_code,
            elapsed_ms,
        )

    return response


# 挂载前端静态资源与 SPA 路由支持
static_dir = Path(__file__).resolve().parents[1] / "static"
index_html_path = static_dir / "index.html"

if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/auth/check")
async def auth_check():
    """检查认证状态，用于前端判断是否需要登录。"""
    from aistudio_api.config import settings

    return {"auth_enabled": settings.auth_enabled}


@app.middleware("http")
async def spa_navigation_middleware(request: Request, call_next):
    """统一处理前端 SPA 单页应用导航：浏览器页面直达/刷新响应 index.html，API 请求直通 JSON。"""
    accept = request.headers.get("accept", "")
    is_html_nav = (
        request.method == "GET"
        and "text/html" in accept
        and "application/json" not in accept
    )
    is_api = request.url.path.startswith(
        (
            "/api",
            "/v1beta",
            "/auth",
            "/assets",
            "/static",
            "/health",
            "/stats",
            "/rotation",
            "/config",
            "/api-keys",
        )
    )
    is_accounts_spa = request.url.path in ("/accounts", "/accounts/")
    if is_html_nav and (is_accounts_spa or not is_api) and index_html_path.is_file():
        response = FileResponse(index_html_path)
        response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        return response

    response = await call_next(request)

    if request.url.path.startswith(("/accounts", "/api/accounts")) or is_html_nav:
        existing_vary = response.headers.get("Vary")
        if existing_vary:
            if "Accept" not in [v.strip() for v in existing_vary.split(",")]:
                response.headers["Vary"] = f"{existing_vary}, Accept"
        else:
            response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )

    return response


@app.get("/")
async def root_index():
    """根路径兜底响应。"""
    if index_html_path.is_file():
        response = FileResponse(index_html_path)
        response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        return response
    return JSONResponse({"message": "AI Studio API Web UI"})


def main():
    from aistudio_api.config import settings

    parser = argparse.ArgumentParser(description="AI Studio API Server")
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()

    import uvicorn

    logger.info("启动服务: 监听端口=%d, 日志级别=%s", args.port, get_log_level())
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port,
        log_level=get_log_level().lower(),
        log_config=None,
        access_log=False,
    )
