from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from aistudio_api.api.dependencies import require_api_key
from aistudio_api.api.routes_models import router as models_router
from aistudio_api.config import settings


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(models_router, dependencies=[Depends(require_api_key)])
    return TestClient(app)


def test_list_models_returns_gemini_format(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    client = _build_client()

    response = client.get("/v1beta/models")
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


def test_get_single_model_returns_model_object(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    client = _build_client()

    response = client.get("/v1beta/models/gemini-3.7-flash")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "models/gemini-3.7-flash"
    assert "displayName" in data
    assert "supportedGenerationMethods" in data


def test_v1_models_endpoint_is_removed(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    client = _build_client()

    response = client.get("/v1/models")
    assert response.status_code == 404


def test_models_endpoint_accepts_query_param_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"my-key"}))
    client = _build_client()

    # Unauthorized without key
    res_unauth = client.get("/v1beta/models")
    assert res_unauth.status_code == 401

    # Authorized with ?key=
    res_auth = client.get("/v1beta/models?key=my-key")
    assert res_auth.status_code == 200
    assert "models" in res_auth.json()
