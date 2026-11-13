"""System and metadata routes."""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import (
    HealthResponse,
    ModelStatsResponse,
    StatsResponse,
    StatsTotalsResponse,
)
from aistudio_api.infrastructure.gateway.model_defaults import (
    _resolve_config_path,
    invalidate_config_cache,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("routes.system")

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState

public_router = APIRouter()
protected_router = APIRouter()


def health_response() -> HealthResponse:
    return HealthResponse(status="ok")


def stats_response() -> StatsResponse:
    from aistudio_api.api.response_models import AccountBreakdownItem
    from aistudio_api.api.state import runtime_state
    from aistudio_api.infrastructure.gateway.model_defaults import (
        resolve_tier_quota_limits,
    )
    stats = dict(runtime_state.model_stats)
    rotator = runtime_state.rotator
    account_svc = runtime_state.account_service
    accounts_list = account_svc.list_accounts() if account_svc else []

    totals = StatsTotalsResponse(
        requests=sum(s.requests for s in stats.values()),
        success=sum(s.success for s in stats.values()),
        rate_limited=sum(s.rate_limited for s in stats.values()),
        errors=sum(s.errors for s in stats.values()),
        prompt_tokens=sum(s.prompt_tokens for s in stats.values()),
        completion_tokens=sum(s.completion_tokens for s in stats.values()),
        reasoning_tokens=sum(s.reasoning_tokens for s in stats.values()),
        cached_tokens=sum(s.cached_tokens for s in stats.values()),
        total_tokens=sum(s.total_tokens for s in stats.values()),
    )

    models = {}
    for name, s in stats.items():
        clean_m = name.removeprefix("models/").lower()
        breakdown: list[AccountBreakdownItem] = []

        if rotator:
            from aistudio_api.application.account_rotator import get_pacific_date_key

            current_la = get_pacific_date_key()
            for acc in accounts_list:
                st = rotator.get_account_stats(acc.id)
                if st:
                    st._check_date_reset(current_la)
                    if clean_m in st.daily_models:
                        m_daily = st.daily_models[clean_m]
                        limits = resolve_tier_quota_limits(acc.tier, clean_m)
                        quota = limits.daily_limit
                        token_quota = limits.daily_token_limit
                        req_pct = round((m_daily.requests / max(1, quota)) * 100, 1)
                        tok_pct = (
                            round((m_daily.total_tokens / token_quota) * 100, 1)
                            if token_quota and token_quota > 0
                            else 0.0
                        )
                        used_pct = max(req_pct, tok_pct)
                        cd_rem = (
                            max(0, int(m_daily.cooldown_until - time.time()))
                            if m_daily.cooldown_until > time.time()
                            else 0
                        )
                        breakdown.append(
                            AccountBreakdownItem(
                                account_id=acc.id,
                                name=acc.name,
                                auth_user=acc.auth_user,
                                email=acc.email,
                                tier=acc.tier,
                                group=acc.group,
                                enabled=acc.enabled,
                                today_requests=m_daily.requests,
                                today_success=m_daily.success,
                                today_rate_limited=m_daily.rate_limited,
                                today_tokens=m_daily.total_tokens,
                                today_reasoning_tokens=m_daily.reasoning_tokens,
                                configured_quota=quota,
                                configured_token_quota=token_quota,
                                quota_used_percent=used_pct,
                                token_quota_used_percent=tok_pct,
                                is_available=acc.enabled
                                and cd_rem == 0
                                and not st.session_expired,
                                cooldown_remaining=cd_rem,
                            )
                        )

        models[name] = ModelStatsResponse(
            requests=s.requests,
            success=s.success,
            rate_limited=s.rate_limited,
            errors=s.errors,
            prompt_tokens=s.prompt_tokens,
            completion_tokens=s.completion_tokens,
            reasoning_tokens=s.reasoning_tokens,
            cached_tokens=s.cached_tokens,
            total_tokens=s.total_tokens,
            finish_reasons=dict(s.finish_reasons),
            last_used=s.last_used,
            account_breakdown=breakdown,
        )
    return StatsResponse(models=models, totals=totals)


@public_router.get("/health", response_model=HealthResponse)
async def health():
    return health_response()


@protected_router.get("/stats", response_model=StatsResponse)
async def stats():
    return stats_response()


# ========== 调度与配额管理 API ==========


class ClearCooldownRequest(BaseModel):
    account_id: str | None = None
    model: str | None = None


@protected_router.get("/rotation")
async def get_rotation_status(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """获取账号黏性调度与配额状态。"""
    rotator = runtime_state.rotator
    if rotator is None:
        return {
            "enabled": False,
            "mode": "sticky",
            "message": "调度器未初始化",
            "accounts": {},
        }

    return {
        "enabled": True,
        "mode": "sticky",
        "accounts": rotator.get_all_stats(),
    }


@protected_router.post("/rotation/clear-cooldown")
async def clear_cooldown(
    req: ClearCooldownRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """清除指定账号或全局的 429 锁定状态。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    if req.account_id:
        rotator.clear_cooldown(req.account_id, model=req.model)
    else:
        rotator.clear_all_cooldowns()
    logger.info(
        "已清除冷却状态: 账号=%s, 模型=%s",
        req.account_id or "全部",
        req.model or "全部",
    )

    return {"ok": True, "accounts": rotator.get_all_stats()}


@protected_router.post("/rotation/cleanup-ghosts")
async def cleanup_ghosts(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """主动对齐并彻底清除已删除账号在调度器中残留的幽灵状态。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    cleaned_ids = rotator.cleanup_ghost_accounts()
    return {
        "ok": True,
        "cleaned_count": len(cleaned_ids),
        "cleaned_ids": cleaned_ids,
        "accounts": rotator.get_all_stats(),
    }


@protected_router.get("/rotation/accounts")
async def get_rotation_accounts(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, dict[str, object]]:
    """获取所有账号的调度与配额统计。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    return rotator.get_all_stats()


@protected_router.post("/rotation/next")
async def force_next_account(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """强制切换到下一个可用账号。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    next_account = await rotator.get_next_account()
    if next_account is None:
        raise HTTPException(404, detail="没有可用的账号")

    account_service = runtime_state.account_service
    client = runtime_state.client

    if account_service is None or client is None or client.session is None:
        raise HTTPException(503, detail="服务未就绪")

    result = await account_service.activate_account(
        next_account.id,
        client.session,
    )

    if result is None:
        raise HTTPException(500, detail="切换失败")

    logger.info("手动切换至下一账号: id=%s, name=%s", result.id, result.name)
    return {
        "ok": True,
        "account": {
            "id": result.id,
            "name": result.name,
            "email": result.email,
        },
    }


# ========== 系统与模型配置 API ==========


class ConfigYamlUpdateRequest(BaseModel):
    yaml_content: str


@protected_router.get("/config")
async def get_system_config() -> dict[str, object]:
    """获取系统运行配置与 config.yaml 内容。"""
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.utils.logger import (
        get_dump_dir,
        get_log_level,
        is_debug_env_active,
        is_dump_requests_enabled,
        is_dump_to_file_enabled,
    )

    config_yaml_path = _resolve_config_path(None)
    yaml_content = ""
    if config_yaml_path.exists():
        with contextlib.suppress(Exception):
            yaml_content = config_yaml_path.read_text(encoding="utf-8")
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_active_waa_server,
    )

    active_waa = get_active_waa_server()
    return {
        "port": settings.port,
        "proof_server_url": str(active_waa.get("url") or settings.proof_server_url),
        "active_signer_server": active_waa.get("id"),
        "proxy_configured": bool(settings.proxy_url),
        "auth_enabled": settings.auth_enabled,
        "yaml_content": yaml_content,
        "log_level": get_log_level(),
        "dump_requests": is_dump_requests_enabled(),
        "debug_env_active": is_debug_env_active(),
        "dump_to_file": is_dump_to_file_enabled(),
        "dump_dir": str(get_dump_dir()),
    }


@protected_router.get("/config/defaults")
async def get_system_config_defaults() -> dict[str, object]:
    """获取系统内置推荐默认配置（订阅额度、付费准入规则、调度策略、模型规则）。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_default_config_sections,
    )

    return get_default_config_sections()


@protected_router.put("/config/yaml")
async def update_config_yaml(req: ConfigYamlUpdateRequest) -> dict[str, object]:
    """更新 config.yaml 文件内容并热重载默认配置。"""

    try:
        parsed = yaml.safe_load(req.yaml_content)
        if parsed is not None and not isinstance(parsed, dict):
            raise HTTPException(400, detail="YAML 顶层必须为字典结构")
    except yaml.YAMLError as e:
        raise HTTPException(400, detail=f"YAML 语法格式错误: {e}") from e
    if isinstance(parsed, dict):
        from aistudio_api.infrastructure.gateway.model_defaults import (
            validate_config_sections,
        )

        try:
            validate_config_sections(parsed)
        except ValueError as e:
            raise HTTPException(400, detail=f"配置校验失败: {e}") from e
    config_yaml_path = _resolve_config_path(None)
    try:
        config_yaml_path.write_text(req.yaml_content, encoding="utf-8")
        invalidate_config_cache()

        if isinstance(parsed, dict) and "logging" in parsed:
            raw_logging = parsed["logging"]
            if isinstance(raw_logging, dict):
                from aistudio_api.infrastructure.utils.logger import (
                    set_dump_dir,
                    set_dump_requests,
                    set_dump_to_file,
                    set_log_level,
                )

                if "level" in raw_logging:
                    set_log_level(str(raw_logging["level"]))
                if "dump_requests" in raw_logging:
                    set_dump_requests(bool(raw_logging["dump_requests"]))
                if "dump_to_file" in raw_logging:
                    set_dump_to_file(bool(raw_logging["dump_to_file"]))
                if "dump_dir" in raw_logging:
                    set_dump_dir(str(raw_logging["dump_dir"]))
        logger.info("已更新并重载 config.yaml")
        return {"ok": True, "message": "配置已保存并重载"}
    except Exception as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e


# ========== WAA 签名服务器管理与纯手动切换 API ==========


class SwitchSignerServerRequest(BaseModel):
    server_id: str


class UpsertSignerServerRequest(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    url: str
    user_agent: str = ""
    headers: dict[str, str] = {}


class TestSignerServerRequest(BaseModel):
    url: str


@protected_router.get("/config/signer-servers")
async def get_signer_servers() -> dict[str, object]:
    """获取所有配置的 WAA 签名服务器列表及当前激活项。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_waa_settings,
    )

    waa = get_configured_waa_settings()
    active_id = str(waa.get("active_server") or "local_spidermonkey").strip()
    raw_servers = waa.get("servers")
    servers = raw_servers if isinstance(raw_servers, list) else []

    result_servers = []
    for s in servers:
        if isinstance(s, dict):
            s_id = str(s.get("id") or "").strip()
            item = dict(s)
            item["is_active"] = s_id == active_id
            result_servers.append(item)

    return {
        "active_server": active_id,
        "servers": result_servers,
    }


@protected_router.post("/config/signer-servers/switch")
async def switch_signer_server(req: SwitchSignerServerRequest) -> dict[str, object]:
    """纯手动切换当前激活的 WAA 签名服务器。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        switch_active_waa_server,
    )

    active = switch_active_waa_server(req.server_id)
    logger.info(
        "切换 WAA 签名服务: id=%s, url=%s",
        active.get("id"),
        active.get("url"),
    )
    return {"ok": True, "active_server": active}


@protected_router.put("/config/signer-servers")
async def upsert_signer_server(req: UpsertSignerServerRequest) -> dict[str, object]:
    """添加或修改 WAA 签名服务器配置。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        upsert_waa_server,
    )

    server_data = req.model_dump(exclude_none=True)
    saved = upsert_waa_server(server_data)
    logger.info(
        "保存 WAA 签名服务器配置: id=%s, name=%s",
        saved.get("id"),
        saved.get("name"),
    )
    return {"ok": True, "server": saved}


@protected_router.delete("/config/signer-servers/{server_id}")
async def delete_signer_server(server_id: str) -> dict[str, bool]:
    """删除指定的 WAA 签名服务器。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        delete_waa_server,
    )

    success = delete_waa_server(server_id)
    if not success:
        raise HTTPException(404, detail="未找到该签名服务器")
    logger.info("删除 WAA 签名服务器: id=%s", server_id)
    return {"ok": True}


@protected_router.post("/config/signer-servers/test")
async def test_signer_server(req: TestSignerServerRequest) -> dict[str, object]:
    """测试指定签名服务的连通性与健康状态。"""
    import time

    import httpx

    from aistudio_api.config import settings

    target_url = req.url.rstrip("/")
    is_local = "127.0.0.1" in target_url or "localhost" in target_url
    proxy = None if is_local else settings.proxy_url

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=8.0, proxy=proxy) as client:
            resp = await client.get(f"{target_url}/health")
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            is_ok = resp.status_code == 200
            data = resp.json() if is_ok else {}
            return {
                "ok": is_ok,
                "status_code": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "data": data,
            }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "ok": False,
            "error": str(e),
            "elapsed_ms": elapsed_ms,
        }


class LoggingConfigRequest(BaseModel):
    level: str | None = None
    dump_requests: bool | None = None
    dump_to_file: bool | None = None
    dump_dir: str | None = None


@protected_router.put("/config/logging")
async def update_logging_config(req: LoggingConfigRequest) -> dict[str, object]:
    """更新系统日志级别与请求转储配置，并持久化到 config.yaml。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        update_configured_logging_settings,
    )
    from aistudio_api.infrastructure.utils.logger import (
        get_dump_dir,
        get_log_level,
        is_debug_env_active,
        is_dump_requests_enabled,
        is_dump_to_file_enabled,
        set_dump_dir,
        set_dump_requests,
        set_dump_to_file,
        set_log_level,
    )

    if req.level is not None:
        set_log_level(req.level)
    if req.dump_requests is not None:
        set_dump_requests(req.dump_requests)
    if req.dump_to_file is not None:
        set_dump_to_file(req.dump_to_file)
    if req.dump_dir is not None:
        set_dump_dir(req.dump_dir)

    update_configured_logging_settings(
        level=req.level,
        dump_requests=req.dump_requests,
        dump_to_file=req.dump_to_file,
        dump_dir=req.dump_dir,
    )
    logger.info(
        "日志配置已更新: 级别=%s, 请求转储=%s, 文件转储=%s, 目录=%s",
        req.level,
        req.dump_requests,
        req.dump_to_file,
        req.dump_dir,
    )

    return {
        "ok": True,
        "log_level": get_log_level(),
        "dump_requests": is_dump_requests_enabled(),
        "debug_env_active": is_debug_env_active(),
        "dump_to_file": is_dump_to_file_enabled(),
        "dump_dir": str(get_dump_dir()),
    }


# ========== API Key 备注与密钥管理 ==========


class ApiKeyItemModel(BaseModel):
    name: str = "API Key"
    key: str = ""
    created_at: str | None = None


class CreateApiKeyRequest(BaseModel):
    name: str = "API Key"
    key: str | None = None


class UpdateApiKeyRequest(BaseModel):
    name: str


@protected_router.get("/api-keys", response_model=list[ApiKeyItemModel])
async def list_api_keys() -> list[ApiKeyItemModel]:
    """获取 config.yaml 中配置的 API Keys 列表。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_api_key_items,
    )

    items = get_configured_api_key_items()
    return [
        ApiKeyItemModel(
            name=item.get("name") or "API Key",
            key=item.get("key") or "",
            created_at=item.get("created_at") or None,
        )
        for item in items
    ]


@protected_router.post("/api-keys", response_model=ApiKeyItemModel)
async def create_api_key(req: CreateApiKeyRequest) -> ApiKeyItemModel:
    """在 config.yaml 中添加新的 API Key。"""
    import secrets
    from datetime import UTC, datetime

    new_key = (
        req.key.strip()
        if req.key and req.key.strip()
        else f"sk-aistudio-{secrets.token_hex(16)}"
    )
    created_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    name = req.name.strip() if req.name.strip() else "API Key"

    config_path = _resolve_config_path(None)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        parsed = {}

    raw_keys = parsed.get("api_keys")
    new_list: list[dict[str, str]] = []
    if isinstance(raw_keys, list):
        for item in raw_keys:
            if isinstance(item, dict):
                new_list.append(
                    {
                        "name": str(item.get("name") or "API Key"),
                        "key": str(item.get("key") or ""),
                        "created_at": str(item.get("created_at") or ""),
                    }
                )
            elif isinstance(item, str) and item.strip():
                new_list.append(
                    {"name": "API Key", "key": item.strip(), "created_at": ""}
                )

    new_list.append({"name": name, "key": new_key, "created_at": created_at})
    parsed["api_keys"] = new_list

    config_path.write_text(
        yaml.dump(parsed, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    invalidate_config_cache()

    logger.info("创建 API Key: 备注=%s, 前缀=%s", name, new_key[:8] + "...")
    return ApiKeyItemModel(name=name, key=new_key, created_at=created_at)


@protected_router.delete("/api-keys/{key_value:path}")
async def delete_api_key(key_value: str) -> dict[str, bool]:
    """在 config.yaml 中删除指定 API Key。"""

    config_path = _resolve_config_path(None)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        raise HTTPException(404, detail="未找到 API Key")

    raw_keys = parsed.get("api_keys")
    new_list: list[dict[str, str]] = []
    found = False
    if isinstance(raw_keys, list):
        for item in raw_keys:
            item_key = str(item.get("key") if isinstance(item, dict) else item).strip()
            if item_key == key_value:
                found = True
                continue
            if isinstance(item, dict):
                new_list.append(
                    {
                        "name": str(item.get("name") or "API Key"),
                        "key": item_key,
                        "created_at": str(item.get("created_at") or ""),
                    }
                )
            elif item_key:
                new_list.append({"name": "API Key", "key": item_key, "created_at": ""})

    if not found:
        raise HTTPException(404, detail="未找到该 API Key")

    parsed["api_keys"] = new_list
    config_path.write_text(
        yaml.dump(parsed, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    invalidate_config_cache()

    logger.info("删除 API Key: 前缀=%s", key_value[:8] + "...")
    return {"ok": True}


@protected_router.put("/api-keys/{key_value:path}", response_model=ApiKeyItemModel)
async def update_api_key_name(
    key_value: str, req: UpdateApiKeyRequest
) -> ApiKeyItemModel:
    """在 config.yaml 中更新指定 API Key 的备注名。"""

    config_path = _resolve_config_path(None)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        raise HTTPException(404, detail="未找到 API Key")

    raw_keys = parsed.get("api_keys")
    new_list: list[dict[str, str]] = []
    updated_item: ApiKeyItemModel | None = None
    new_name = req.name.strip() if req.name.strip() else "API Key"

    if isinstance(raw_keys, list):
        for item in raw_keys:
            item_key = str(item.get("key") if isinstance(item, dict) else item).strip()
            created = str(item.get("created_at") if isinstance(item, dict) else "")
            if item_key == key_value:
                updated_item = ApiKeyItemModel(
                    name=new_name, key=item_key, created_at=created or None
                )
                new_list.append(
                    {"name": new_name, "key": item_key, "created_at": created}
                )
            else:
                name_val = str(
                    item.get("name") if isinstance(item, dict) else "API Key"
                )
                new_list.append(
                    {"name": name_val, "key": item_key, "created_at": created}
                )

    if not updated_item:
        raise HTTPException(404, detail="未找到该 API Key")

    parsed["api_keys"] = new_list
    config_path.write_text(
        yaml.dump(parsed, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    invalidate_config_cache()
    logger.info("更新 API Key 备注: 前缀=%s, 备注=%s", key_value[:8] + "...", new_name)

    return updated_item
