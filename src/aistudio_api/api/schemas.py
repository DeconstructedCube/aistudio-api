"""HTTP request schemas."""

from __future__ import annotations


from typing import Any
from pydantic import BaseModel


class GeminiInlineData(BaseModel):
    mimeType: str
    data: str


class GeminiFileData(BaseModel):
    mimeType: str | None = None
    fileUri: str


class GeminiPart(BaseModel):
    text: str | None = None
    inlineData: GeminiInlineData | None = None
    fileData: GeminiFileData | None = None
    thought: bool | None = None
    thoughtSignature: str | None = None


class GeminiContent(BaseModel):
    role: str | None = None
    parts: list[GeminiPart]


class GeminiTool(BaseModel):
    codeExecution: dict[str, Any] | None = None
    googleSearch: dict[str, Any] | None = None
    googleSearchRetrieval: dict[str, Any] | None = None
    googleMaps: dict[str, Any] | None = None
    urlContext: dict[str, Any] | None = None
    functionDeclarations: list[dict[str, Any]] | None = None

class GeminiGenerationConfig(BaseModel):
    stopSequences: list[str] | None = None
    temperature: float | None = None
    topP: float | None = None
    topK: int | None = None
    maxOutputTokens: int | None = None
    responseModalities: list[str] | None = None
    responseMimeType: str | None = None
    responseSchema: list[object] | dict[str, object] | None = None
    presencePenalty: float | None = None
    frequencyPenalty: float | None = None
    responseLogprobs: bool | None = None
    logprobs: int | None = None
    mediaResolution: list[object] | int | str | None = None
    thinkingConfig: list[object] | dict[str, object] | None = None
    imageConfig: dict[str, object] | None = None


class GeminiSafetySetting(BaseModel):
    category: str
    threshold: str


class GeminiGenerateContentRequest(BaseModel):
    contents: list[GeminiContent]
    systemInstruction: GeminiContent | None = None
    tools: list[GeminiTool] | None = None
    generationConfig: GeminiGenerationConfig | None = None
    safetySettings: list[GeminiSafetySetting] | None = None
