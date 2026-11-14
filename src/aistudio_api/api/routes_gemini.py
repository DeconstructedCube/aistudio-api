"""Gemini-compatible API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aistudio_api.api.response_models import (
    GeminiCountTokensResponse,
    GeminiCreateDecisionResponse,
    GeminiGenerateContentResponse,
)
from aistudio_api.application.api_service_gemini import (
    handle_gemini_count_tokens,
    handle_gemini_decisions,
    handle_gemini_generate_content,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

from .dependencies import get_client
from .schemas import (
    GeminiCountTokensRequest,
    GeminiCreateDecisionRequest,
    GeminiGenerateContentRequest,
)

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
    logger.debug("收到 generateContent 请求: model=%s", model_path)
    return await handle_gemini_generate_content(model_path, req, client, stream=False)


@router.post("/v1beta/{model_path:path}:streamGenerateContent")
async def stream_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient = Depends(get_client),
):
    logger.debug("收到 streamGenerateContent 请求: model=%s", model_path)
    return await handle_gemini_generate_content(model_path, req, client, stream=True)


@router.post(
    "/v1beta/{model_path:path}:countTokens",
    response_model=GeminiCountTokensResponse,
)
async def count_tokens(
    model_path: str,
    req: GeminiCountTokensRequest,
    client: AIStudioClient = Depends(get_client),
):
    logger.debug("收到 countTokens 请求: model=%s", model_path)
    return await handle_gemini_count_tokens(model_path, req, client)


@router.post(
    "/v1beta/decisions",
    response_model=GeminiCreateDecisionResponse,
)
async def create_decision(
    req: GeminiCreateDecisionRequest,
    client: AIStudioClient = Depends(get_client),
):
    logger.debug("收到 decisions 请求: model=%s", req.model)
    return await handle_gemini_decisions(req, client)
