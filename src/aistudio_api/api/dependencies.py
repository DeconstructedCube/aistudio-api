"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.client import AIStudioClient

from .state import runtime_state


def _extract_request_token(request: Request) -> str | None:
    # 1. Google Gemini standard query param: ?key=...
    query_key = request.query_params.get("key")
    if query_key and query_key.strip():
        return query_key.strip()

    # 2. Google Gemini standard header: x-goog-api-key
    goog_key = (request.headers.get("x-goog-api-key") or "").strip()
    if goog_key:
        return goog_key

    # 3. Standard x-api-key header
    api_key = (request.headers.get("x-api-key") or "").strip()
    if api_key:
        return api_key

    # 4. Bearer token in Authorization header
    authorization = (request.headers.get("authorization") or "").strip()
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None

    token = token.strip()
    return token or None


def require_web_auth(request: Request) -> None:
    """保护管理控制台路由 (通过环境变量 AISTUDIO_WEB_PASSWORD 配置密码)。"""
    if not settings.auth_enabled:
        return

    # 允许浏览器直接加载 HTML 入口页面，由前端 Vue 路由守卫拦截并引导至登录页
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return

    token = _extract_request_token(request)
    if (
        token
        and settings.web_password
        and settings.web_password.strip()
        and secrets.compare_digest(token, settings.web_password)
    ):
        return
    raise HTTPException(
        status_code=401,
        detail="管理控制台鉴权失败，请输入正确的管理密码",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_api_key(request: Request) -> None:
    """保护对外 Gemini API 路由 (通过 config.yaml 中的 api_keys 配置)。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_api_keys,
    )

    configured_keys = get_configured_api_keys()
    all_valid_keys = configured_keys | settings.api_keys

    # 如果 config.yaml 与环境变量均未配置任何 Key，则 API 免密开放
    if not all_valid_keys:
        return

    token = _extract_request_token(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Invalid or missing API key. Pass via 'x-goog-api-key' header, 'key' query parameter, or 'Authorization: Bearer <key>'",
                "type": "authentication_error",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token in all_valid_keys:
        return

    # 匹配管理密码（管理员具备超级 API 访问权限）
    if (
        token
        and settings.web_password
        and settings.web_password.strip()
        and secrets.compare_digest(token, settings.web_password)
    ):
        return
    raise HTTPException(
        status_code=401,
        detail={
            "message": "Invalid API key provided",
            "type": "authentication_error",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_client() -> AIStudioClient:
    if runtime_state.client is None:
        raise HTTPException(
            503,
            detail={"message": "Client not initialized", "type": "service_unavailable"},
        )
    return runtime_state.client


def get_account_service():
    if runtime_state.account_service is None:
        raise HTTPException(
            503,
            detail={
                "message": "Account service not initialized",
                "type": "service_unavailable",
            },
        )
    return runtime_state.account_service


def get_account_service_optional():
    return runtime_state.account_service


def get_runtime_state():
    return runtime_state
