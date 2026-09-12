"""Models and metadata routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import GeminiModelListResponse, GeminiModelResponse

router = APIRouter()


def _to_gemini_model(m: dict) -> GeminiModelResponse:
    raw_id = m.get("id") or m.get("name", "")
    clean_id = raw_id.removeprefix("models/")
    name = f"models/{clean_id}"
    display_name = m.get("displayName") or clean_id
    description = m.get("description") or f"Google {display_name} model"
    input_tokens = m.get("inputTokenLimit") or 1048576
    output_tokens = m.get("outputTokenLimit") or 8192
    methods = m.get("supportedGenerationMethods") or [
        "generateContent",
        "countTokens",
        "createCachedContent",
    ]
    return GeminiModelResponse(
        name=name,
        version="001",
        displayName=display_name,
        description=description,
        inputTokenLimit=input_tokens,
        outputTokenLimit=output_tokens,
        supportedGenerationMethods=methods,
        temperature=1.0,
        topP=0.95,
        topK=64,
    )


@router.get("/v1beta/models", response_model=GeminiModelListResponse)
async def list_models(
    runtime_state=Depends(get_runtime_state),
):
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery
    discovered = await model_discovery.get_models(session=session)
    models = [_to_gemini_model(m) for m in discovered]
    return GeminiModelListResponse(models=models)


@router.get("/v1beta/models/{model_id:path}", response_model=GeminiModelResponse)
async def get_model(
    model_id: str,
    runtime_state=Depends(get_runtime_state),
):
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery
    discovered = await model_discovery.get_models(session=session)
    clean_target = model_id.removeprefix("models/")
    for m in discovered:
        m_clean = (m.get("id") or m.get("name", "")).removeprefix("models/")
        if m_clean == clean_target:
            return _to_gemini_model(m)
    return _to_gemini_model({"id": clean_target})
