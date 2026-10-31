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
    from aistudio_api.application.account_rotator import init_rotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.browser.browser_engine import (
        install_process_cleanup_handlers,
    )

    install_process_cleanup_handlers()

    client = AIStudioClient(
        port=runtime_state.browser_port,
    )
    runtime_state.client = client

    # 初始化账号管理服务
    account_store = AccountStore()
    account_service = AccountService(account_store)
    runtime_state.account_service = account_service

    # 初始化黏性账号调度器
    rotator = init_rotator(account_store)
    runtime_state.rotator = rotator

    for a in account_store.list_accounts():
        st = rotator.get_account_stats(a.id)
        if st is not None and not st.session_expired and st.auth_cooldown > 0:
            st.auth_cooldown = 0.0
    active_auth_path = account_store.get_active_auth_path()
    if active_auth_path is not None and client.session is not None:
        client.session._auth_file = str(active_auth_path)
    logger.info(
        "服务初始化完成: 监听浏览器调试端口=%d, 已加载账号数=%d",
        runtime_state.browser_port,
        len(account_store.list_accounts()),
    )
    logger.debug(
        "账号初始状态: %s",
        [
            f"{a.id}(u/{a.auth_user},tier={a.tier},enabled={a.enabled})"
            for a in account_store.list_accounts()
        ],
    )

    # 后台预热浏览器，避免首次请求延迟
    warmup_task = None

    async def _warmup():
        for attempt in range(3):
            try:
                logger.debug("开始预热浏览器后端: attempt=%d", attempt + 1)
                await client.warmup()
                logger.debug("浏览器预热就绪: attempt=%d", attempt + 1)
                return
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(1.0)
                    continue
                logger.warning("浏览器预热失败: %s", e)

    warmup_task = asyncio.create_task(_warmup())

    tier_task = None

    async def _auto_refresh_tiers():
        import json

        from aistudio_api.infrastructure.account.cookie_parser import (
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
            logger.debug("所有账号邮箱与订阅等级最近均已校验，跳过启动探测")
            return

        logger.info(
            "开始自动校验账号订阅等级与邮箱 (共 %d 个账号待检测)...",
            len(accounts_to_check),
        )
        upgraded_count = 0
        checked_count = 0

        for a in accounts_to_check:
            apath = account_store.get_auth_path(a.id)
            if apath and apath.is_file():
                try:
                    adata = json.loads(apath.read_text(encoding="utf-8"))
                    rcookies = adata.get("cookies") or []
                    cdict = {
                        str(c.get("name") or ""): str(c.get("value") or "")
                        for c in rcookies
                        if isinstance(c, dict) and c.get("name")
                    }
                    if cdict:
                        checked_count += 1
                        if not a.email:
                            em = await probe_account_email(
                                cdict, auth_user=a.auth_user or "0"
                            )
                            if em:
                                account_store.update_account_email(a.id, em)
                        t = await probe_account_benefit_tier(
                            cdict, auth_user=a.auth_user or "0"
                        )
                        resolved_tier = t or "Free"
                        account_store.update_account_tier(
                            a.id, resolved_tier, checked_at=now_ts
                        )
                        if resolved_tier != "Free" and a.tier == "Free":
                            upgraded_count += 1
                            logger.info(
                                "账号订阅等级自动升级: id=%s (u/%s) -> %s",
                                a.id,
                                a.auth_user,
                                resolved_tier,
                            )
                except Exception as e:
                    logger.debug("账号 %s 订阅检测异常: %s", a.id, e)

        logger.info(
            "账号订阅校验完成: 检测=%d 个, 发现付费订阅=%d 个",
            checked_count,
            upgraded_count,
        )

    tier_task = asyncio.create_task(_auto_refresh_tiers())

    yield
    logger.info("服务正在关闭")
    if warmup_task and not warmup_task.done():
        warmup_task.cancel()
    if tier_task and not tier_task.done():
        tier_task.cancel()
    if client:
        try:
            await client.close()
        except Exception as e:
            logger.debug("关闭客户端失败: %s", e)
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
            "HTTP 异常: status=%d, message=%s, path=%s",
            code,
            msg,
            _request.url.path,
        )
    elif code in (401, 403, 429):
        logger.warning(
            "HTTP 权限/限额异常: status=%d, message=%s, path=%s",
            code,
            msg,
            _request.url.path,
        )
    else:
        logger.info(
            "HTTP 业务异常: status=%d, message=%s, path=%s",
            code,
            msg,
            _request.url.path,
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
    parser.add_argument("--browser-port", type=int, default=settings.browser_port)
    args = parser.parse_args()

    runtime_state.browser_port = args.browser_port

    import uvicorn

    logger.info("启动服务: 监听端口=%d, 浏览器调试端口=%d", args.port, runtime_state.browser_port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
