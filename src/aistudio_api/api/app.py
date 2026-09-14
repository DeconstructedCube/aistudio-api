"""FastAPI application entrypoint."""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from aistudio_api.infrastructure.gateway.client import AIStudioClient

from .dependencies import require_api_key, require_web_auth
from .routes_accounts import router as accounts_router
from .routes_gemini import router as gemini_router
from .routes_models import router as models_router
from .routes_system import protected_router as system_protected_router
from .routes_system import public_router as system_public_router
from .state import runtime_state

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger("aistudio.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from aistudio_api.application.account_rotator import RotationMode, init_rotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.account.account_store import AccountStore

    client = AIStudioClient(
        port=runtime_state.browser_port,
    )
    runtime_state.client = client
    from aistudio_api.config import settings as app_settings

    runtime_state.busy_lock = asyncio.Semaphore(app_settings.max_concurrency)

    # 注入 snapshot 缓存引用，切号时需要清除
    from aistudio_api.infrastructure.gateway.client import _snapshot_cache

    runtime_state.snapshot_cache = _snapshot_cache

    # 初始化账号管理服务
    account_store = AccountStore()
    account_service = AccountService(account_store)
    runtime_state.account_service = account_service

    # 初始化账号轮询器
    rotation_mode = getattr(settings, "account_rotation_mode", "round_robin")
    cooldown = getattr(settings, "account_cooldown_seconds", 60)
    rotator = init_rotator(
        account_store,
        mode=RotationMode(rotation_mode),
        cooldown_seconds=cooldown,
    )
    runtime_state.rotator = rotator

    logger.info(
        "Client initialized (port=%s, rotation=%s, accounts=%d)",
        runtime_state.browser_port,
        rotator.mode,
        len(account_store.list_accounts()),
    )

    # 后台预热浏览器，避免首次请求延迟
    warmup_task = None

    async def _warmup():
        try:
            await client.warmup()
        except Exception as e:
            logger.warning("浏览器预热失败: %s", e)

    warmup_task = asyncio.create_task(_warmup())

    yield
    logger.info("Shutting down")
    if warmup_task and not warmup_task.done():
        warmup_task.cancel()
    if client:
        try:
            await client.close()
        except Exception as e:
            logger.debug("Error closing client: %s", e)
    runtime_state.client = None
    runtime_state.busy_lock = None
    runtime_state.account_service = None
    runtime_state.rotator = None


app = FastAPI(title="AI Studio API", lifespan=lifespan)
app.include_router(system_public_router)
app.include_router(system_protected_router, dependencies=[Depends(require_web_auth)])
app.include_router(accounts_router, dependencies=[Depends(require_web_auth)])
app.include_router(gemini_router, dependencies=[Depends(require_api_key)])
app.include_router(models_router, dependencies=[Depends(require_api_key)])

# 挂载前端静态资源与 SPA 路由支持
from pathlib import Path
from fastapi.responses import FileResponse

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


@app.get("/")
@app.get("/login")
@app.get("/accounts")
@app.get("/settings")
async def serve_spa():
    """为前端 SPA 提供统一入口页面。"""
    if index_html_path.is_file():
        return FileResponse(index_html_path)
    return {"message": "AI Studio API Web UI"}
def main():
    from aistudio_api.config import settings

    parser = argparse.ArgumentParser(description="AI Studio API Server")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--browser-port", type=int, default=settings.browser_port)
    args = parser.parse_args()

    runtime_state.browser_port = args.browser_port

    import uvicorn

    logger.info("Starting server on port %s", args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
