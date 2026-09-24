"""Gemini-compatible API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.application.api_service import handle_gemini_generate_content
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

from .dependencies import get_client
from .schemas import GeminiGenerateContentRequest

logger = get_logger("routes.gemini")
router = APIRouter()


@router.post(
    "/v1beta/{model_path:path}:generateContent",
    response_model=GeminiGenerateContentResponse,
)
async def generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient = Depends(get_client),
):
    logger.info("收到 Gemini generateContent 请求: model=%s", model_path)
    return await handle_gemini_generate_content(model_path, req, client, stream=False)


@router.post("/v1beta/{model_path:path}:streamGenerateContent")
async def stream_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient = Depends(get_client),
):
    logger.info("收到 Gemini streamGenerateContent 流式请求: model=%s", model_path)
    return await handle_gemini_generate_content(model_path, req, client, stream=True)
