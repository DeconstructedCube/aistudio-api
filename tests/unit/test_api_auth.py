import httpx
import pytest
from fastapi import Depends, FastAPI

from aistudio_api.api.dependencies import require_api_key
from aistudio_api.config import settings


def _build_client() -> httpx.AsyncClient:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def protected():
        return {"ok": True}

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_auth_is_disabled_when_no_api_key_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/protected")
        assert response.status_code == 200
        assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_bearer_token_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_client() as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Bearer secret-token"}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_x_api_key_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_client() as client:
        response = await client.get("/protected", headers={"X-API-Key": "secret-token"})
        assert response.status_code == 200
        assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_x_goog_api_key_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_client() as client:
        response = await client.get(
            "/protected", headers={"x-goog-api-key": "secret-token"}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_query_param_key_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_client() as client:
        response = await client.get("/protected?key=secret-token")
        assert response.status_code == 200
        assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_missing_or_invalid_api_key_returns_401(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_client() as client:
        response = await client.get("/protected")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["detail"]["type"] == "authentication_error"
