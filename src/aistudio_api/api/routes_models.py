"""Models and metadata routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import (
    GeminiModelListResponse,
    GeminiModelResponse,
)

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState

router = APIRouter()


def _to_gemini_model(m: dict[str, object]) -> GeminiModelResponse:
    raw_id = str(m.get("id") or m.get("name") or "")
    clean_id = raw_id.removeprefix("models/")
    name = f"models/{clean_id}"
    display_name = str(m.get("displayName") or clean_id)
    description = str(m.get("description") or f"Google {display_name} model")
    input_tokens = int(str(m.get("inputTokenLimit") or 1048576))
    output_tokens = int(str(m.get("outputTokenLimit") or 8192))
    raw_methods = m.get("supportedGenerationMethods")
    methods = (
        [str(x) for x in raw_methods]
        if isinstance(raw_methods, list)
        else [
            "generateContent",
            "countTokens",
            "createCachedContent",
        ]
    )
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
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelListResponse:
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    discovered = await model_discovery.get_models(session=session)
    models = [_to_gemini_model(m) for m in discovered]
    return GeminiModelListResponse(models=models)


@router.get("/v1beta/models/{model_id:path}", response_model=GeminiModelResponse)
async def get_model(
    model_id: str,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelResponse:
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    discovered = await model_discovery.get_models(session=session)
    clean_target = model_id.removeprefix("models/")
    for m in discovered:
        m_raw = str(m.get("id") or m.get("name") or "")
        m_clean = m_raw.removeprefix("models/")
        if m_clean == clean_target:
            return _to_gemini_model(m)
    return _to_gemini_model({"id": clean_target})
