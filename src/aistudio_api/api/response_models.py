"""Structured HTTP response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class GeminiUsageMetadata(BaseModel):
    promptTokenCount: int = 0
    candidatesTokenCount: int = 0
    thoughtsTokenCount: int = 0
    totalTokenCount: int = 0
    cachedContentTokenCount: int | None = None


class GeminiFunctionCallPayload(BaseModel):
    name: str
    args: object | None = None
    id: str | None = None


class GeminiFunctionResponsePayload(BaseModel):
    name: str
    response: object | None = None
    id: str | None = None


class GeminiInlineDataResponse(BaseModel):
    mimeType: str
    data: str


class GeminiPartResponse(BaseModel):
    text: str | None = None
    thought: bool | None = None
    inlineData: GeminiInlineDataResponse | None = None
    thoughtSignature: str | None = None
    functionCall: GeminiFunctionCallPayload | None = None
    functionResponse: GeminiFunctionResponsePayload | None = None


class GeminiContentResponse(BaseModel):
    role: Literal["model"] = "model"
    parts: list[GeminiPartResponse]


class GeminiCandidateResponse(BaseModel):
    content: GeminiContentResponse
    finishReason: str | None = None
    index: int = 0


class GeminiGenerateContentResponse(BaseModel):
    candidates: list[GeminiCandidateResponse]
    usageMetadata: GeminiUsageMetadata | None = None
    modelVersion: str | None = None
    responseId: str | None = None


class HealthResponse(BaseModel):
    status: str
    busy: bool


class AccountBreakdownItem(BaseModel):
    account_id: str
    name: str
    auth_user: str = "0"
    email: str | None = None
    tier: str = "Free"
    group: str = "default"
    enabled: bool = True
    today_requests: int = 0
    today_success: int = 0
    today_rate_limited: int = 0
    today_tokens: int = 0
    today_reasoning_tokens: int = 0
    configured_quota: int = 100
    quota_used_percent: float = 0.0
    is_available: bool = True
    cooldown_remaining: int = 0


class ModelStatsResponse(BaseModel):
    requests: int
    success: int
    rate_limited: int
    errors: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int
    finish_reasons: dict[str, int] = {}
    last_used: str | None = None
    account_breakdown: list[AccountBreakdownItem] = []


class StatsTotalsResponse(BaseModel):
    requests: int
    success: int
    rate_limited: int
    errors: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int


class StatsResponse(BaseModel):
    models: dict[str, ModelStatsResponse]
    totals: StatsTotalsResponse


class GeminiModelResponse(BaseModel):
    name: str
    version: str | None = "001"
    displayName: str | None = None
    description: str | None = None
    inputTokenLimit: int | None = None
    outputTokenLimit: int | None = None
    supportedGenerationMethods: list[str] = [
        "generateContent",
        "countTokens",
        "createCachedContent",
    ]
    temperature: float | None = None
    topP: float | None = None
    topK: int | None = None


class GeminiModelListResponse(BaseModel):
    models: list[GeminiModelResponse]
