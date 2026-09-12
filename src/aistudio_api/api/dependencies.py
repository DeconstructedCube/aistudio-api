"""Shared FastAPI dependencies."""

from __future__ import annotations

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


def require_api_key(request: Request) -> None:
    if not settings.auth_enabled:
        return

    token = _extract_request_token(request)
    if token in settings.api_keys:
        return

    raise HTTPException(
        status_code=401,
        detail={
            "message": "Invalid or missing API key. Pass via 'x-goog-api-key' header, 'key' query parameter, or 'Authorization: Bearer <key>'",
            "type": "authentication_error",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_client() -> AIStudioClient:
    if runtime_state.client is None:
        raise HTTPException(503, detail={"message": "Client not initialized", "type": "service_unavailable"})
    return runtime_state.client


def get_busy_lock():
    if runtime_state.busy_lock is None:
        raise HTTPException(503, detail={"message": "Server not ready", "type": "service_unavailable"})
    return runtime_state.busy_lock


def get_account_service():
    if runtime_state.account_service is None:
        raise HTTPException(503, detail={"message": "Account service not initialized", "type": "service_unavailable"})
    return runtime_state.account_service


def get_runtime_state():
    return runtime_state
