"""Models and metadata routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import (
    GeminiModelListResponse,
    GeminiModelResponse,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("routes.models")

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState
router = APIRouter()


def _to_gemini_model(model_data: Mapping[str, object]) -> GeminiModelResponse:
    raw_id = str(model_data.get("id") or model_data.get("name") or "")
    clean_id = raw_id.removeprefix("models/")
    name = f"models/{clean_id}"
    display_name = str(model_data.get("displayName") or clean_id)
    description = str(model_data.get("description") or f"Google {display_name} model")
    input_tokens = int(str(model_data.get("inputTokenLimit") or 1048576))
    output_tokens = int(str(model_data.get("outputTokenLimit") or 8192))
    raw_methods = model_data.get("supportedGenerationMethods")
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
    models = [_to_gemini_model(item) for item in discovered]
    logger.info("获取模型列表完成 (共发现 %d 个)", len(models))
    return GeminiModelListResponse(models=models)


@router.get("/v1beta/models/{model_id:path}", response_model=GeminiModelResponse)
async def get_model(
    model_id: str,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelResponse:
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    model_info = await model_discovery.get_model_by_id(model_id, session=session)
    if model_info is not None:
        logger.info("成功获取模型元数据: %s", model_id)
        return _to_gemini_model(model_info)
    raise HTTPException(status_code=404, detail="Model not found")
