"""OpenAI-compatible API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from aistudio_api.application.api_service import handle_chat, handle_image_edit, handle_image_generation
from aistudio_api.api.response_models import (
    ImageGenerationResponse,
    ModelCardResponse,
    ModelListResponse,
    OpenAIChatCompletionResponse,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient

from .dependencies import get_client
from .schemas import ChatRequest, ImageRequest

router = APIRouter()

MODELS = [
    # Gemma 4 系列
    {"id": "gemma-4-31b-it", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemma-4-26b-a4b-it", "object": "model", "created": 1700000000, "owned_by": "google"},
    # Gemini 3 系列
    {"id": "gemini-3-flash-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.5-flash", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-pro-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-lite", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-image-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3-pro-image-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-live-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-tts-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # Gemini 2.5 系列
    # {"id": "gemini-2.5-pro", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash-lite", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash-image", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-pro-preview-tts", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash-preview-tts", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Gemini 2.0 系列
    # {"id": "gemini-2.0-flash-lite", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Latest 别名
    {"id": "gemini-pro-latest", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-flash-latest", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-flash-lite-latest", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Deep Research
    # {"id": "deep-research-preview-04-2026", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "deep-research-max-preview-04-2026", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # 图片生成 (Imagen)
    # {"id": "imagen-4.0-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "imagen-4.0-ultra-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "imagen-4.0-fast-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # 视频生成 (Veo)
    # {"id": "veo-3.1-generate-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "veo-3.1-fast-generate-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "veo-3.1-lite-generate-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "veo-2.0-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # 音乐生成 (Lyria)
    # {"id": "lyria-3-pro-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "lyria-3-clip-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Robotics
    # {"id": "gemini-robotics-er-1.6-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
] 
MODELS = [ModelCardResponse(**model) for model in MODELS]

MODEL_IDS = {m.id for m in MODELS}


@router.get("/v1/models", response_model=ModelListResponse)
async def list_models(
    runtime_state=Depends(get_runtime_state),
):
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery
    discovered = await model_discovery.get_models(session=session)
    cards = [
        ModelCardResponse(
            id=m["id"],
            object="model",
            created=1700000000,
            owned_by="google",
        )
        for m in discovered
    ]
    return ModelListResponse(data=cards)


@router.get("/v1/models/{model_id:path}", response_model=ModelCardResponse)
async def get_model(
    model_id: str,
    runtime_state=Depends(get_runtime_state),
):
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery
    discovered = await model_discovery.get_models(session=session)
    for m in discovered:
        if m["id"] == model_id or m.get("name") == model_id:
            return ModelCardResponse(
                id=m["id"],
                object="model",
                created=1700000000,
                owned_by="google",
            )
    return ModelCardResponse(id=model_id, object="model", created=1700000000, owned_by="google")


@router.post("/v1/chat/completions", response_model=OpenAIChatCompletionResponse)
async def chat_completions(req: ChatRequest, client: AIStudioClient = Depends(get_client)):
    return await handle_chat(req, client)


@router.post("/v1/images/generations", response_model=ImageGenerationResponse)
async def image_generations(req: ImageRequest, client: AIStudioClient = Depends(get_client)):
    return await handle_image_generation(req, client)


@router.post("/v1/images/edits", response_model=ImageGenerationResponse)
async def image_edits(
    prompt: str = Form(...),
    image: list[UploadFile] | None = File(None),
    mask: UploadFile | None = File(None),
    model: str = Form("gemini-3.1-flash-image-preview"),
    n: int = Form(1),
    size: str = Form("1024x1024"),
    client: AIStudioClient = Depends(get_client),
):
    return await handle_image_edit(
        prompt=prompt,
        image_files=image or [],
        mask_file=mask,
        model=model,
        n=n,
        size=size,
        client=client,
    )
