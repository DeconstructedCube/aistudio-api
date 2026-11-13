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
    get_logger,
    is_dump_requests_enabled,
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

    # 启动时自动检查并拉起本地 WAA 签名服务（若配置为本地节点）
    ensure_signer_ready()

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
                        checked_count += 1
                        if not a.email:
                            em = await probe_account_email(
                                cdict,
                                auth_user=a.auth_user or "0",
                                client=probe_client,
                            )
                            if em:
                                account_store.update_account_email(a.id, em)
                        t = await probe_account_benefit_tier(
                            cdict,
                            auth_user=a.auth_user or "0",
                            client=probe_client,
                        )
                        resolved_tier = t or "Free"
                        account_store.update_account_tier(
                            a.id, resolved_tier, checked_at=now_ts
                        )
                        if resolved_tier != "Free" and a.tier == "Free":
                            upgraded_count += 1
                            logger.info(
                                "账号订阅等级更新: id=%s (u/%s) -> %s",
                                a.id,
                                a.auth_user,
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
    for bg_task in (tier_task, rotate_task):
        if bg_task and not bg_task.done():
            bg_task.cancel()
    if client:
        try:
            await client.close()
        except Exception as e:
            logger.debug("关闭客户端异常: %s", e)
    stop_signer_daemon()
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


@app.middleware("http")
async def logging_and_dump_middleware(request, call_next):
    """统一请求生命周期日志与请求转储 (DEBUG) 中间件。"""

    start_time = time.perf_counter()
    path = request.url.path

    is_static = path.startswith(("/static", "/assets")) or path in ("/favicon.ico",)
    dump_enabled = is_dump_requests_enabled() and not is_static

    req_id = ""
    body_text = None
    client_str = ""
    headers_dict = None
    query_dict = None

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

    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
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
            )
        else:
            logger.error(
                "HTTP %s %s -> 500 (%.1fms, %s)",
                request.method,
                path,
                elapsed_ms,
                exc,
            )
        raise exc

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    content_type = response.headers.get("content-type", "")
    is_event_stream = "text/event-stream" in content_type

    if dump_enabled:
        resp_text = None
        if is_event_stream:
            resp_text = "(streaming response in progress)"
        else:
            try:
                # 读取非流式响应体并重构 iterator 以便正常返回给客户端
                chunks = [chunk async for chunk in response.body_iterator]
                resp_bytes = b"".join(chunks)
                resp_text = resp_bytes.decode("utf-8", errors="replace")

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
            is_stream=is_event_stream,
        )
    elif not is_static:
        logger.info(
            "HTTP %s %s -> %d (%.1fms)",
            request.method,
            path,
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

    logger.info("启动服务: 监听端口=%d", args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
