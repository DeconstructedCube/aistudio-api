"""账号管理路由。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_account_service, get_account_service_optional, get_runtime_state
from aistudio_api.infrastructure.account.cookie_parser import parse_cookie_string

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState
    from aistudio_api.application.account_service import AccountService
log = logging.getLogger("aistudio.routes_accounts")

router = APIRouter(prefix="/accounts")


class AccountResponse(BaseModel):
    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None
    auth_user: str = "0"
    cookie_id: str | None = None


class UpdateAccountRequest(BaseModel):
    name: str


class ImportCookiesRequest(BaseModel):
    cookies: str
    name: str | None = None
    email: str | None = None
    account_id: str | None = None
    auth_user: str = "0"


class ImportCookiesResponse(BaseModel):
    account_id: str
    name: str
    cookie_count: int
    domain_summary: dict[str, int]
    auth_user: str = "0"


class ProbeAndImportRequest(BaseModel):
    cookies: str
    name_prefix: str | None = None


class ProbeAndImportResponse(BaseModel):
    imported_count: int
    accounts: list[AccountResponse]


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    request: Request,
    account_service: AccountService | None = Depends(get_account_service_optional),
) -> Response | list[AccountResponse]:
    """列出所有账号或作为浏览器导航入口。"""
    if "text/html" in request.headers.get("accept", ""):
        static_dir = Path(__file__).resolve().parents[1] / "static"
        index_html = static_dir / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)

    if account_service is None:
        raise HTTPException(
            503,
            detail={
                "message": "Account service not initialized",
                "type": "service_unavailable",
            },
        )
    accounts = account_service.list_accounts()
    return [
        AccountResponse(
            id=a.id,
            name=a.name,
            email=a.email,
            created_at=a.created_at,
            last_used=a.last_used,
            auth_user=getattr(a, "auth_user", "0"),
            cookie_id=getattr(a, "cookie_id", None) or f"cookie_{a.created_at[:16]}",
        )
        for a in accounts
    ]


@router.get("/active", response_model=AccountResponse)
async def get_active_account(
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """获取当前活跃账号。"""
    account = account_service.get_active_account()
    if account is None:
        raise HTTPException(status_code=404, detail="没有活跃账号")
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at,
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0"),
        cookie_id=getattr(account, "cookie_id", None) or f"cookie_{account.created_at[:16]}",
    )


@router.post("/{account_id}/activate", response_model=AccountResponse)
async def activate_account(
    account_id: str,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """切换到指定账号。"""
    browser_session = runtime_state.client._session if runtime_state.client else None
    snapshot_cache = runtime_state.snapshot_cache

    if browser_session is None:
        raise HTTPException(status_code=503, detail="服务未就绪")

    account = await account_service.activate_account(
        account_id, browser_session, snapshot_cache
    )
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在或切换失败")
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at,
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0"),
        cookie_id=getattr(account, "cookie_id", None) or f"cookie_{account.created_at[:16]}",
    )


@router.delete("/{account_id}")
async def delete_account(
    account_id: str,
    account_service: AccountService = Depends(get_account_service),
) -> dict[str, bool]:
    """删除账号。"""
    success = account_service.delete_account(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"ok": True}


@router.delete("/group/{cookie_id}")
async def delete_cookie_group(
    cookie_id: str,
    account_service: AccountService = Depends(get_account_service),
) -> dict[str, int]:
    """按 Cookie 组批量删除该 Cookie 下的所有子账号。"""
    accounts = account_service.list_accounts()
    deleted_count = 0
    for a in accounts:
        acc_cid = getattr(a, "cookie_id", None) or f"cookie_{a.created_at[:16]}"
        if acc_cid == cookie_id:
            if account_service.delete_account(a.id):
                deleted_count += 1
    return {"deleted": deleted_count}


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str,
    req: UpdateAccountRequest,
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """更新账号名称。"""
    account = account_service.update_account(account_id, req.name)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at,
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0"),
    )


@router.post("/import-cookies", response_model=ImportCookiesResponse)
async def import_cookies(
    req: ImportCookiesRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ImportCookiesResponse:
    """从 cookie 导入账号（支持 JSON 数组、Netscape 或 KV）。"""
    storage_state = parse_cookie_string(req.cookies)
    raw_cookies = storage_state.get("cookies")
    cookie_list: list[dict[str, object]] = (
        raw_cookies if isinstance(raw_cookies, list) else []
    )
    cookie_count = len(cookie_list)

    if cookie_count == 0:
        raise HTTPException(status_code=400, detail="未解析到有效 cookie")

    domain_summary: dict[str, int] = {}
    for c in cookie_list:
        d = str(c.get("domain", ""))
        domain_summary[d] = domain_summary.get(d, 0) + 1
    name = req.name or (
        f"Google Account (u/{req.auth_user})" if req.auth_user != "0" else "导入的账号"
    )

    import secrets
    cid = f"cookie_{secrets.token_hex(4)}"
    account = account_service._store.save_account(
        name=name,
        email=req.email,
        storage_state=storage_state,
        account_id=req.account_id,
        auth_user=req.auth_user or "0",
        cookie_id=cid,
    )
    try:
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if browser_session:
            auth_path = account_service._store.get_auth_path(account.id)
            count = await browser_session.import_cookies(
                req.cookies,
                auth_file=str(auth_path) if auth_path else None,
            )
            log.info("[import-cookies] injected %d cookies, saved auth.json", count)
    except Exception as e:
        log.warning("[import-cookies] browser injection failed: %s", e)

    return ImportCookiesResponse(
        account_id=account.id,
        name=account.name,
        cookie_count=cookie_count,
        domain_summary=domain_summary,
        auth_user=account.auth_user,
    )


@router.post("/probe-import", response_model=ProbeAndImportResponse)
async def probe_and_import(
    req: ProbeAndImportRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ProbeAndImportResponse:
    """单份 Cookie 无限向下探活多账号并一键批量导入。"""
    from aistudio_api.infrastructure.account.cookie_parser import (
        parse_cookie_string,
        probe_google_accounts_infinite,
    )

    probed = await probe_google_accounts_infinite(req.cookies)
    if not probed:
        raise HTTPException(status_code=400, detail="未探测到有效已登录 Google 账号")

    storage_state = parse_cookie_string(req.cookies)
    imported_accounts: list[AccountResponse] = []
    prefix = req.name_prefix.strip() if req.name_prefix else "Google Account"
    import secrets
    cid = f"cookie_{secrets.token_hex(4)}"
    for p in probed:
        u_idx = str(p["auth_user"])
        acc_name = (
            f"{prefix} (u/{u_idx})" if len(probed) > 1 or u_idx != "0" else prefix
        )
        account = account_service._store.save_account(
            name=acc_name,
            email=None,
            storage_state=storage_state,
            auth_user=u_idx,
            cookie_id=cid,
        )
        imported_accounts.append(
            AccountResponse(
                id=account.id,
                name=account.name,
                email=account.email,
                created_at=account.created_at,
                last_used=account.last_used,
                auth_user=account.auth_user,
                cookie_id=cid,
            )
        )

    # 如果当前没有激活的账号，默认激活第一个
    if not account_service.get_active_account() and imported_accounts:
        account_service.set_active_account(imported_accounts[0].id)

    return ProbeAndImportResponse(
        imported_count=len(imported_accounts),
        accounts=imported_accounts,
    )
