"""HTTP request schemas."""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel

class GeminiInlineData(BaseModel):
    mimeType: str
    data: str


class GeminiFileData(BaseModel):
    mimeType: Optional[str] = None
    fileUri: str


class GeminiPart(BaseModel):
    text: Optional[str] = None
    inlineData: Optional[GeminiInlineData] = None
    fileData: Optional[GeminiFileData] = None
    thought: Optional[bool] = None
    thoughtSignature: Optional[str] = None


class GeminiContent(BaseModel):
    role: Optional[str] = None
    parts: list[GeminiPart]


class GeminiTool(BaseModel):
    codeExecution: Optional[dict[str, Any]] = None
    googleSearch: Optional[dict[str, Any]] = None
    googleSearchRetrieval: Optional[dict[str, Any]] = None
    googleMaps: Optional[dict[str, Any]] = None
    urlContext: Optional[dict[str, Any]] = None
    functionDeclarations: Optional[list[dict[str, Any]]] = None


class GeminiGenerationConfig(BaseModel):
    stopSequences: Optional[list[str]] = None
    temperature: Optional[float] = None
    topP: Optional[float] = None
    topK: Optional[int] = None
    maxOutputTokens: Optional[int] = None
    responseModalities: Optional[list[str]] = None
    responseMimeType: Optional[str] = None
    responseSchema: Optional[list[Any] | dict[str, Any]] = None
    presencePenalty: Optional[float] = None
    frequencyPenalty: Optional[float] = None
    responseLogprobs: Optional[bool] = None
    logprobs: Optional[int] = None
    mediaResolution: Optional[list[Any] | int | str] = None
    thinkingConfig: Optional[list[Any] | dict[str, Any]] = None
    imageConfig: Optional[dict[str, Any]] = None


class GeminiSafetySetting(BaseModel):
    category: str
    threshold: str


class GeminiGenerateContentRequest(BaseModel):
    contents: list[GeminiContent]
    systemInstruction: Optional[GeminiContent] = None
    tools: Optional[list[GeminiTool]] = None
    generationConfig: Optional[GeminiGenerationConfig] = None
    safetySettings: Optional[list[GeminiSafetySetting]] = None

