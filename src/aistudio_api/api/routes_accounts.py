"""账号管理路由。"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends, HTTPException, Path as FastApiPath
from pydantic import BaseModel, Field

from aistudio_api.api.dependencies import (
    get_account_service,
    get_runtime_state,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.account.cookie_parser import (
    parse_cookie_string,
    probe_account_benefit_tier,
    probe_account_email,
)
from aistudio_api.infrastructure.utils.common import mask_email

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
    tier: str = "Free"
    enabled: bool = True
    group: str = "default"
    notes: str = ""


def to_account_response(account: AccountMeta) -> AccountResponse:
    cid = getattr(account, "cookie_id", None) or (
        f"cookie_{account.created_at[:16]}"
        if getattr(account, "created_at", None)
        else f"cookie_{account.id}"
    )
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at or "",
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0") or "0",
        cookie_id=cid,
        tier=getattr(account, "tier", "Free") or "Free",
        enabled=getattr(account, "enabled", True),
        group=getattr(account, "group", "default") or "default",
        notes=getattr(account, "notes", "") or "",
    )


class UpdateAccountRequest(BaseModel):
    name: str


class UpdateGroupRequest(BaseModel):
    group: str


class UpdateNotesRequest(BaseModel):
    notes: str


class ToggleAccountRequest(BaseModel):
    enabled: bool | None = None


class PresetResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    action: str = "snapshot"
    account_ids: list[str] = []
    target_tiers: list[str] = []
    target_groups: list[str] = []
    is_system: bool = False
    created_at: str = ""
    updated_at: str = ""


class CreatePresetRequest(BaseModel):
    name: str
    description: str = ""
    action: str = "snapshot"
    account_ids: list[str] = []
    target_tiers: list[str] = []
    target_groups: list[str] = []


class SnapshotPresetRequest(BaseModel):
    name: str
    description: str = ""


class ApplyPresetResponse(BaseModel):
    ok: bool = True
    preset_id: str
    preset_name: str
    enabled_count: int
    disabled_count: int
    enabled_account_ids: list[str]
    disabled_account_ids: list[str]


class ImportCookiesRequest(BaseModel):
    cookies: str
    name: str | None = None
    email: str | None = None
    account_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_\-\.@]+$")
    auth_user: str = "0"
    tier: str | None = None


class ImportCookiesResponse(BaseModel):
    account_id: str
    name: str
    cookie_count: int
    domain_summary: dict[str, int]
    auth_user: str = "0"
    tier: str = "Free"


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
    tier: str | None = None


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
    return [to_account_response(a) for a in accounts]


@router.get("/active", response_model=AccountResponse)
async def get_active_account(
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """获取当前活跃账号。"""
    account = account_service.get_active_account()
    if account is None:
        raise HTTPException(status_code=404, detail="没有活跃账号")
    return to_account_response(account)


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
    if runtime_state.rotator:
        runtime_state.rotator.add_account(account.id)
        runtime_state.rotator.clear_cooldown(account.id)
    log.info("激活账号: id=%s, name=%s", account.id, account.name)
    return to_account_response(account)


@router.post("/{account_id}/detect-email", response_model=AccountResponse)
async def detect_account_email(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """自动获取并更新指定账号的真实邮箱与订阅等级。"""
    import json

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    target_auth = account_service.get_account_auth_path(account_id)
    browser_session = runtime_state.client._session if runtime_state.client else None
    email: str | None = None
    detected_tier: str | None = None

    # 1. 若受控浏览器当前正好处于该账号上下文，优先直接从当前页面提取（免切号）
    is_current_session = bool(
        browser_session is not None
        and browser_session.page is not None
        and (
            not target_auth
            or not getattr(browser_session, "_auth_file", None)
            or str(target_auth) == browser_session._auth_file
        )
    )
    if is_current_session and browser_session is not None:
        with contextlib.suppress(Exception):
            email = await browser_session.extract_account_email()
        if hasattr(browser_session, "extract_account_tier"):
            with contextlib.suppress(Exception):
                detected_tier = await browser_session.extract_account_tier()

    # 2. 通过 auth.json 凭据执行高速 HTTP 探测（无需切号，即使浏览器休眠也可用）
    if (
        (not email or not detected_tier or detected_tier == "Free")
        and target_auth
        and target_auth.is_file()
    ):
        try:
            auth_data = json.loads(target_auth.read_text(encoding="utf-8"))
            raw_cookies = auth_data.get("cookies") or []
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies
                if isinstance(c, dict) and c.get("name")
            }
            if cookie_dict:
                if not email:
                    email = await probe_account_email(
                        cookie_dict, auth_user=acc.auth_user or "0"
                    )
                if not detected_tier or detected_tier == "Free":
                    http_tier = await probe_account_benefit_tier(
                        cookie_dict, auth_user=acc.auth_user or "0"
                    )
                    if (http_tier and http_tier != "Free") or not detected_tier:
                        detected_tier = http_tier
        except Exception as e:
            log.debug("通过 HTTP 探测账号 %s 邮箱/等级失败: %s", account_id, e)

    # 3. 若 HTTP 仍未取到邮箱且受控浏览器可用，回退切换浏览器会话提取
    if not email and browser_session is not None and browser_session.page is not None:
        if target_auth and str(target_auth) != browser_session._auth_file:
            await browser_session.switch_auth(str(target_auth))
        email = await browser_session.extract_account_email()
        if (not detected_tier or detected_tier == "Free") and hasattr(
            browser_session, "extract_account_tier"
        ):
            with contextlib.suppress(Exception):
                detected_tier = await browser_session.extract_account_tier()

    if email:
        account_service.update_account_email(account_id, email)
        log.info("已更新账号邮箱: id=%s, email=%s", account_id, mask_email(email))

    if detected_tier:
        account_service.update_account_tier(account_id, detected_tier)
        log.info("已更新账号订阅等级: id=%s, tier=%s", account_id, detected_tier)

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return to_account_response(acc)


@router.post("/{account_id}/detect-tier", response_model=AccountResponse)
async def detect_account_tier(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """探测并更新指定账号的订阅权益等级 (Free / Pro / Ultra / Plus)。"""
    import json

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    detected_tier: str | None = None
    target_auth = account_service.get_account_auth_path(account_id)
    browser_session = runtime_state.client._session if runtime_state.client else None
    if (
        browser_session is not None
        and browser_session.page is not None
        and target_auth
        and str(target_auth) == browser_session._auth_file
        and hasattr(browser_session, "extract_account_tier")
    ):
        detected_tier = await browser_session.extract_account_tier()

    if not detected_tier and target_auth and target_auth.is_file():
        try:
            auth_data = json.loads(target_auth.read_text(encoding="utf-8"))
            raw_cookies = auth_data.get("cookies") or []
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies
                if isinstance(c, dict) and c.get("name")
            }
            if cookie_dict:
                detected_tier = await probe_account_benefit_tier(
                    cookie_dict, auth_user=acc.auth_user or "0"
                )
        except Exception as e:
            log.debug("通过 HTTP 探测账号 %s 订阅等级失败: %s", account_id, e)

    if detected_tier:
        account_service.update_account_tier(account_id, detected_tier)
        log.info("已更新账号订阅等级: id=%s, tier=%s", account_id, detected_tier)

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return to_account_response(acc)


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

    if runtime_state.rotator:
        runtime_state.rotator.remove_account(account_id)
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

    log.info("已删除账号: id=%s", account_id)
    return {"ok": True}


@router.delete("/group/{cookie_id}")
async def delete_cookie_group(
    cookie_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, int]:
    """按 Cookie 组批量删除该 Cookie 下的所有子账号。"""
    active_account = account_service.get_active_account()
    active_id = active_account.id if active_account is not None else None
    active_deleted = False

    accounts = account_service.list_accounts()
    deleted_count = 0
    rotator = runtime_state.rotator
    for a in accounts:
        acc_cid = getattr(a, "cookie_id", None) or f"cookie_{a.created_at[:16]}"
        if acc_cid == cookie_id:
            if a.id == active_id:
                active_deleted = True
            if account_service.delete_account(a.id):
                deleted_count += 1
                if rotator:
                    rotator.remove_account(a.id)
                log.info("已删除子账号: cookie_id=%s, id=%s", cookie_id, a.id)

    if active_deleted:
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
    log.info("账号名称已更新: id=%s, name=%s", account.id, req.name)
    return to_account_response(account)


@router.post("/{account_id}/toggle", response_model=AccountResponse)
async def toggle_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: ToggleAccountRequest = Body(default_factory=ToggleAccountRequest),
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """切换单个账号的使能状态 (Enabled/Disabled)。"""
    account = account_service.toggle_account_enabled(account_id, req.enabled)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    # 如果刚好关闭了当前活跃账号，顺延切到下一个可用账号
    active_acc = account_service.get_active_account()
    if active_acc and active_acc.id == account_id and not account.enabled:
        from aistudio_api.application.account_orchestrator import try_switch_account

        await try_switch_account(failed_account_id=account_id)

    log.info("账号状态已切换: id=%s, enabled=%s", account.id, account.enabled)
    return to_account_response(account)


@router.put("/{account_id}/group", response_model=AccountResponse)
async def update_account_group(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: UpdateGroupRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """更新账号所属业务分组。"""
    account = account_service.update_account_group(account_id, req.group)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    log.info("账号分组已更新: id=%s, group=%s", account.id, account.group)
    return to_account_response(account)


@router.put("/{account_id}/notes", response_model=AccountResponse)
async def update_account_notes(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: UpdateNotesRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """更新账号备注。"""
    account = account_service.update_account_notes(account_id, req.notes)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return to_account_response(account)


@router.get("/groups/list", response_model=list[str])
async def list_groups(
    account_service: AccountService = Depends(get_account_service),
) -> list[str]:
    """列出当前存在的所有分组名称。"""
    return account_service.list_groups()


@router.post("/groups/{group_name}/toggle")
async def batch_toggle_group(
    group_name: str,
    enabled: bool = Body(..., embed=True),
    account_service: AccountService = Depends(get_account_service),
) -> dict[str, object]:
    """按分组批量启用或停用账号。"""
    updated = account_service.batch_toggle_group(group_name, enabled)
    active_acc = account_service.get_active_account()
    if active_acc and active_acc.group == group_name and not enabled:
        from aistudio_api.application.account_orchestrator import try_switch_account

        await try_switch_account(failed_account_id=active_acc.id)
    return {"ok": True, "count": len(updated), "group": group_name, "enabled": enabled}


# ─── 预设方案 (Presets) REST 路由 ───
@router.get("/presets/list", response_model=list[PresetResponse])
async def list_presets(
    account_service: AccountService = Depends(get_account_service),
) -> list[PresetResponse]:
    """获取所有可用预设方案列表。"""
    presets = account_service.list_presets()
    return [PresetResponse.model_validate(p.to_dict()) for p in presets]


@router.post("/presets", response_model=PresetResponse)
async def create_preset(
    req: CreatePresetRequest,
    account_service: AccountService = Depends(get_account_service),
) -> PresetResponse:
    """创建自定义预设方案。"""
    import secrets

    from aistudio_api.infrastructure.account.account_store import AccountPreset

    pid = f"preset_{secrets.token_hex(4)}"
    preset = AccountPreset(
        id=pid,
        name=req.name.strip() or "自定义方案",
        description=req.description.strip(),
        action=req.action,
        account_ids=req.account_ids,
        target_tiers=req.target_tiers,
        target_groups=req.target_groups,
        is_system=False,
    )
    saved = account_service.save_preset(preset)
    return PresetResponse.model_validate(saved.to_dict())


@router.post("/presets/snapshot", response_model=PresetResponse)
async def create_snapshot_preset(
    req: SnapshotPresetRequest,
    account_service: AccountService = Depends(get_account_service),
) -> PresetResponse:
    """将当前所有启用账号一键快照保存为新预设方案。"""
    saved = account_service.create_snapshot_preset(req.name, req.description)
    log.info("已创建预设快照: name=%s, id=%s", saved.name, saved.id)
    return PresetResponse.model_validate(saved.to_dict())


@router.delete("/presets/{preset_id}")
async def delete_preset(
    preset_id: str,
    account_service: AccountService = Depends(get_account_service),
) -> dict[str, bool]:
    """删除自定义预设方案。"""
    try:
        ok = account_service.delete_preset(preset_id)
        if not ok:
            raise HTTPException(status_code=404, detail="预设方案不存在")
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/presets/{preset_id}/apply", response_model=ApplyPresetResponse)
async def apply_preset(
    preset_id: str,
    account_service: AccountService = Depends(get_account_service),
) -> ApplyPresetResponse:
    """一键应用预设方案，原子切换账号使能状态。"""
    try:
        preset, en_ids, dis_ids = account_service.apply_preset(preset_id)
        # 检查当前活跃账号是否有效，若失效则顺延切换
        active_acc = account_service.get_active_account()
        from aistudio_api.application.account_orchestrator import try_switch_account

        if not active_acc or not active_acc.enabled or active_acc.id in dis_ids:
            await try_switch_account(
                failed_account_id=active_acc.id if active_acc else None
            )
        else:
            await try_switch_account()

        log.info(
            "已应用预设: name=%s, 启用=%d, 停用=%d",
            preset.name,
            len(en_ids),
            len(dis_ids),
        )
        return ApplyPresetResponse(
            ok=True,
            preset_id=preset.id,
            preset_name=preset.name,
            enabled_count=len(en_ids),
            disabled_count=len(dis_ids),
            enabled_account_ids=en_ids,
            disabled_account_ids=dis_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


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
    detected_email = req.email
    detected_tier = req.tier
    if not detected_email or not detected_tier:
        raw_cookies_list = storage_state.get("cookies")
        if isinstance(raw_cookies_list, list):
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies_list
                if isinstance(c, dict) and c.get("name")
            }
            if cookie_dict:
                if not detected_email:
                    with contextlib.suppress(Exception):
                        detected_email = await probe_account_email(
                            cookie_dict, auth_user=req.auth_user or "0"
                        )
                if not detected_tier:
                    with contextlib.suppress(Exception):
                        probed = await probe_account_benefit_tier(
                            cookie_dict, auth_user=req.auth_user or "0"
                        )
                        if probed and probed != "Free":
                            detected_tier = probed
    detected_tier = detected_tier or "Free"
    account = account_service.save_account_from_cookies(
        name=name,
        email=detected_email,
        storage_state=storage_state,
        account_id=req.account_id,
        auth_user=req.auth_user or "0",
        cookie_id=cid,
        tier=detected_tier,
    )
    if runtime_state.rotator:
        runtime_state.rotator.add_account(account.id)
        runtime_state.rotator.clear_cooldown(account.id)
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
            log.info("已导入 Cookie 并保存文件: 数量=%d, 账号=%s", count, account.name)
    except Exception as e:
        log.warning("为 %s 注入浏览器 Cookie 失败: %s", account.name, e)
    return ImportCookiesResponse(
        account_id=account.id,
        name=account.name,
        cookie_count=cookie_count,
        domain_summary=domain_summary,
        auth_user=account.auth_user,
        tier=account.tier,
    )


@router.post("/probe-import", response_model=ProbeAndImportResponse)
async def probe_and_import(
    req: ProbeAndImportRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ProbeAndImportResponse:
    """探测 Cookie 中的多个已登录账号并批量导入。"""
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
    rotator = getattr(runtime_state, "rotator", None)
    if rotator:
        for meta in metas:
            rotator.add_account(meta.id)
            rotator.clear_cooldown(meta.id)

    imported_accounts = [to_account_response(account) for account in metas]

    client_obj = getattr(runtime_state, "client", None)
    if not had_active_account and metas and client_obj is not None:
        browser_session = client_obj._session
        if browser_session is not None:
            await account_service.activate_account(metas[0].id, browser_session)
    log.info("Cookie 探活完成: 导入账号数=%d", len(imported_accounts))
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
                    "tier": a.tier or "Free",
                }
            )
    elif req.content:
        try:
            raw_data = json.loads(req.content)
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"JSON 内容解析失败: {e}"
            ) from e
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
            raise HTTPException(
                status_code=400, detail=f"读取或解析文件失败: {e}"
            ) from e
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
        raise HTTPException(
            status_code=400, detail="未解析到任何包含有效 cookies 的账号条目"
        )

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
        acc_tier = str(item.get("tier") or "")
        if not acc_email or not acc_tier:
            raw_cookies_list = storage_state.get("cookies")
            if isinstance(raw_cookies_list, list):
                cookie_dict = {
                    str(c.get("name") or ""): str(c.get("value") or "")
                    for c in raw_cookies_list
                    if isinstance(c, dict) and c.get("name")
                }
                if cookie_dict:
                    if not acc_email:
                        with contextlib.suppress(Exception):
                            acc_email = await probe_account_email(
                                cookie_dict, auth_user=acc_auth_user
                            )
                    if not acc_tier:
                        with contextlib.suppress(Exception):
                            probed = await probe_account_benefit_tier(
                                cookie_dict, auth_user=acc_auth_user
                            )
                            if probed and probed != "Free":
                                acc_tier = probed
        acc_tier = acc_tier or "Free"
        cid = f"cookie_{secrets.token_hex(4)}"

        account = account_service.save_account_from_cookies(
            name=acc_name,
            email=acc_email,
            storage_state=storage_state,
            auth_user=acc_auth_user,
            cookie_id=cid,
            tier=acc_tier,
        )
        rotator = getattr(runtime_state, "rotator", None)
        if rotator:
            rotator.add_account(account.id)
            rotator.clear_cooldown(account.id)
        imported_accounts.append(to_account_response(account))
    if not account_service.get_active_account() and imported_accounts:
        account_service.set_active_account(imported_accounts[0].id)
    log.info("批量导入完成: 导入账号数=%d", len(imported_accounts))
    return ImportBundleResponse(
        imported_count=len(imported_accounts),
        accounts=imported_accounts,
    )
