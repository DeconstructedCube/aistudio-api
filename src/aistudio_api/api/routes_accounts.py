"""账号管理路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends, HTTPException, Path as FastApiPath
from pydantic import BaseModel, Field

from aistudio_api.api.dependencies import (
    get_account_service,
    get_runtime_state,
)
from aistudio_api.infrastructure.account.cookie_parser import parse_cookie_string

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState
    from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("accounts")

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
    account_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_\-\.@]+$")
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


class BundleAccountItem(BaseModel):
    id: str | None = None
    name: str | None = None
    email: str | None = None
    cookies: str
    auth_user: str = "0"


class ImportBundleRequest(BaseModel):
    file_path: str | None = None
    content: str | None = None
    accounts: list[BundleAccountItem] | None = None


class ImportBundleResponse(BaseModel):
    imported_count: int
    accounts: list[AccountResponse]

@router.get("", response_model=list[AccountResponse])
@router.get("/", response_model=list[AccountResponse])
async def list_accounts(
    account_service: AccountService = Depends(get_account_service),
) -> list[AccountResponse]:
    """列出所有账号。"""
    accounts = account_service.list_accounts()
    return [
        AccountResponse(
            id=a.id,
            name=a.name,
            email=a.email,
            created_at=a.created_at or "",
            last_used=a.last_used,
            auth_user=getattr(a, "auth_user", "0") or "0",
            cookie_id=getattr(a, "cookie_id", None)
            or (
                f"cookie_{a.created_at[:16]}"
                if getattr(a, "created_at", None)
                else f"cookie_{a.id}"
            ),
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
        created_at=account.created_at or "",
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0") or "0",
        cookie_id=getattr(account, "cookie_id", None)
        or (
            f"cookie_{account.created_at[:16]}"
            if getattr(account, "created_at", None)
            else f"cookie_{account.id}"
        ),
    )


@router.post("/{account_id}/activate", response_model=AccountResponse)
async def activate_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """切换到指定账号。"""
    browser_session = runtime_state.client._session if runtime_state.client else None

    if browser_session is None:
        raise HTTPException(status_code=503, detail="服务未就绪")

    account = await account_service.activate_account(account_id, browser_session)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在或切换失败")
    log.info("手动激活账号: %s (%s)", account.id, account.name)
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at or "",
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0") or "0",
        cookie_id=getattr(account, "cookie_id", None)
        or (
            f"cookie_{account.created_at[:16]}"
            if getattr(account, "created_at", None)
            else f"cookie_{account.id}"
        ),
    )


@router.delete("/{account_id}")
async def delete_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, bool]:
    """删除账号。"""
    active_account = account_service.get_active_account()
    is_active = active_account is not None and active_account.id == account_id

    success = account_service.delete_account(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="账号不存在")

    if is_active:
        remaining_accounts = account_service.list_accounts()
        new_active = remaining_accounts[0] if remaining_accounts else None
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if new_active is not None:
            account_service.set_active_account(new_active.id)
            if browser_session is not None:
                new_auth_path = account_service.get_account_auth_path(new_active.id)
                await browser_session.switch_auth(
                    str(new_auth_path) if new_auth_path else None
                )
        else:
            if browser_session is not None:
                await browser_session.switch_auth(None)

    log.info("已删除账号: %s", account_id)
    return {"ok": True}


@router.delete("/group/{cookie_id}")
async def delete_cookie_group(
    cookie_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, int]:
    """按 Cookie 组批量删除该 Cookie 下的所有子账号。"""
    accounts = account_service.list_accounts()
    active_account = account_service.get_active_account()
    active_deleted = False
    deleted_count = 0
    for a in accounts:
        acc_cid = getattr(a, "cookie_id", None) or f"cookie_{a.created_at[:16]}"
        if acc_cid != cookie_id:
            continue
        if active_account is not None and a.id == active_account.id:
            active_deleted = True
        if account_service.delete_account(a.id):
            deleted_count += 1
            log.info("已删除组 %s 下的子账号 %s", cookie_id, a.id)

    if active_deleted:
        remaining = account_service.list_accounts()
        replacement = remaining[0] if remaining else None
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if replacement is not None:
            account_service.set_active_account(replacement.id)
        if browser_session is not None:
            replacement_path = (
                account_service.get_account_auth_path(replacement.id)
                if replacement is not None
                else None
            )
            await browser_session.switch_auth(
                str(replacement_path) if replacement_path else None
            )
    return {"deleted": deleted_count}


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: UpdateAccountRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """更新账号名称。"""
    account = account_service.update_account(account_id, req.name)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    log.info("账号已更新: %s -> %s", account.id, req.name)
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
    for cookie in cookie_list:
        domain = str(cookie.get("domain", ""))
        domain_summary[domain] = domain_summary.get(domain, 0) + 1
    name = req.name or (
        f"Google Account (u/{req.auth_user})" if req.auth_user != "0" else "导入的账号"
    )

    import secrets

    cid = f"cookie_{secrets.token_hex(4)}"
    account = account_service.save_account_from_cookies(
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
            auth_path = account_service.get_account_auth_path(account.id)
            count = await browser_session.import_cookies(
                req.cookies,
                auth_file=str(auth_path) if auth_path else None,
            )
            log.info("已注入 %d 个 Cookie 并为 %s 保存 auth.json", count, account.name)
    except Exception as e:
        log.warning("为 %s 注入浏览器 Cookie 失败: %s", account.name, e)

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

    had_active_account = account_service.get_active_account() is not None
    probed = await probe_google_accounts_infinite(req.cookies)
    if not probed:
        raise HTTPException(status_code=400, detail="未探测到有效已登录 Google 账号")

    storage_state = parse_cookie_string(req.cookies)
    prefix = req.name_prefix.strip() if req.name_prefix else "Google Account"
    import secrets

    cid = f"cookie_{secrets.token_hex(4)}"
    metas = account_service.batch_import_accounts(
        probed_list=probed,
        storage_state=storage_state,
        prefix=prefix,
        cookie_id=cid,
    )
    imported_accounts = [
        AccountResponse(
            id=account.id,
            name=account.name,
            email=account.email,
            created_at=account.created_at,
            last_used=account.last_used,
            auth_user=account.auth_user,
            cookie_id=account.cookie_id or cid,
        )
        for account in metas
    ]

    if not had_active_account and metas and runtime_state.client is not None:
        browser_session = runtime_state.client._session
        if browser_session is not None:
            await account_service.activate_account(metas[0].id, browser_session)

    log.info("探活与导入完成: 成功导入 %d 个账号", len(imported_accounts))
    return ProbeAndImportResponse(
        imported_count=len(imported_accounts),
        accounts=imported_accounts,
    )


@router.post("/import-bundle", response_model=ImportBundleResponse)
async def import_bundle(
    req: ImportBundleRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ImportBundleResponse:
    """批量导入多个账号的凭据 Bundle（支持传入 JSON 文本、直接解析列表或服务器本地文件路径）。"""
    import json
    import secrets
    from pathlib import Path

    from aistudio_api.infrastructure.account.cookie_parser import parse_cookie_string

    items_to_import: list[dict[str, object]] = []

    if req.accounts:
        for a in req.accounts:
            items_to_import.append(
                {
                    "name": a.name or "Google Account",
                    "email": a.email,
                    "cookies": a.cookies,
                    "auth_user": a.auth_user or "0",
                }
            )
    elif req.content:
        try:
            raw_data = json.loads(req.content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"JSON 内容解析失败: {e}") from e
        if isinstance(raw_data, list):
            raw_list = raw_data
        elif isinstance(raw_data, dict):
            raw_list = raw_data.get("accounts") or raw_data.get("profiles") or []
        else:
            raw_list = []
        for item in raw_list:
            if isinstance(item, dict) and item.get("cookies"):
                items_to_import.append(item)
    elif req.file_path:
        fpath = Path(req.file_path).expanduser().resolve()
        if not fpath.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在: {fpath}")
        try:
            raw_text = fpath.read_text(encoding="utf-8")
            raw_data = json.loads(raw_text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"读取或解析文件失败: {e}") from e
        if isinstance(raw_data, list):
            raw_list = raw_data
        elif isinstance(raw_data, dict):
            raw_list = raw_data.get("accounts") or raw_data.get("profiles") or []
        else:
            raw_list = []
        for item in raw_list:
            if isinstance(item, dict) and item.get("cookies"):
                items_to_import.append(item)
    else:
        raise HTTPException(
            status_code=400,
            detail="请提供 accounts 列表、content JSON 文本或 file_path 本地文件路径",
        )

    if not items_to_import:
        raise HTTPException(status_code=400, detail="未解析到任何包含有效 cookies 的账号条目")

    imported_accounts: list[AccountResponse] = []
    for idx, item in enumerate(items_to_import):
        raw_cookie_str = str(item.get("cookies") or "")
        if not raw_cookie_str:
            continue
        storage_state = parse_cookie_string(raw_cookie_str)
        if not storage_state.get("cookies"):
            continue

        acc_name = str(item.get("name") or f"Account {idx + 1}")
        acc_email = str(item["email"]) if item.get("email") else None
        acc_auth_user = str(item.get("auth_user") or "0")
        cid = f"cookie_{secrets.token_hex(4)}"

        account = account_service.save_account_from_cookies(
            name=acc_name,
            email=acc_email,
            storage_state=storage_state,
            auth_user=acc_auth_user,
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
                cookie_id=account.cookie_id or cid,
            )
        )

    if not account_service.get_active_account() and imported_accounts:
        account_service.set_active_account(imported_accounts[0].id)

    log.info("批量文件导入完成: 成功入库 %d 个账号", len(imported_accounts))
    return ImportBundleResponse(
        imported_count=len(imported_accounts),
        accounts=imported_accounts,
    )
