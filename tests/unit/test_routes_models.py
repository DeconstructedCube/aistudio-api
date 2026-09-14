import httpx
import pytest
from fastapi import Depends, FastAPI

from aistudio_api.api.dependencies import require_api_key
from aistudio_api.api.routes_models import router as models_router
from aistudio_api.config import settings


def _build_client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(models_router, dependencies=[Depends(require_api_key)])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_list_models_returns_gemini_format(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/v1beta/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) > 0

        first = data["models"][0]
        assert first["name"].startswith("models/")
        assert "displayName" in first
        assert "supportedGenerationMethods" in first
        assert "generateContent" in first["supportedGenerationMethods"]


@pytest.mark.anyio
async def test_get_single_model_returns_model_object(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/v1beta/models/gemini-3.7-flash")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "models/gemini-3.7-flash"
        assert "displayName" in data
        assert "supportedGenerationMethods" in data


@pytest.mark.anyio
async def test_v1_models_endpoint_is_removed(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/v1/models")
        assert response.status_code == 404


@pytest.mark.anyio
async def test_models_endpoint_accepts_query_param_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"my-key"}))
    async with _build_client() as client:
        # Unauthorized without key
        res_unauth = await client.get("/v1beta/models")
        assert res_unauth.status_code == 401

        # Authorized with ?key=
        res_auth = await client.get("/v1beta/models?key=my-key")
        assert res_auth.status_code == 200
        assert "models" in res_auth.json()
