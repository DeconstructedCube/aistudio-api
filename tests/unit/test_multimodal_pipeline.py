"""Unit tests for zero-disk multimodal pipeline and image generation tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.client import AIStudioClient


def test_multimodal_zero_disk_io_pipeline():
    """Verify inline data stays in memory with zero temp file creation."""
    fake_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inlineData": {"mimeType": "image/png", "data": fake_base64}},
                        {"text": "Analyze this image"},
                    ],
                }
            ]
        }
    )
    normalized = normalize_gemini_request(req, "gemini-3.5-flash")
    assert normalized.cleanup_paths == []
    part0 = normalized.contents[0].parts[0]
    assert part0.inline_data == ("image/png", fake_base64)
    wire_part = part0.to_wire()
    assert wire_part == [None, None, ["image/png", fake_base64]]


@pytest.mark.asyncio
async def test_generate_image_tools_selection():
    """Verify tool override precedence and use_default_tools handling."""
    client = AIStudioClient(port=9222)
    captured_tools = []

    async def mock_capture_request(*args, **kwargs):
        captured_tools.append(kwargs.get("tools"))
        return CapturedRequest(
            url="http://example.com",
            headers={},
            body='["models/imagen-3.0-generate-002",[]]',
        )

    client.capture_request = mock_capture_request  # type: ignore[method-assign]
    client._replay_service.replay = AsyncMock(return_value=(200, b'{"candidates": []}'))  # type: ignore[method-assign]

    # Explicit google_search=True
    await client.generate_image(
        prompt="draw a cat",
        model="models/gemini-3.1-flash-image-preview",
        google_search=True,
        image_search=False,
    )
    assert captured_tools[-1] == [[None, None, None, [None, [[]]]]]

    # use_default_tools=False -> tools must be None
    await client.generate_image(
        prompt="draw a cat",
        model="models/gemini-3.1-flash-image-preview",
        google_search=False,
        image_search=False,
        use_default_tools=False,
    )
    assert captured_tools[-1] is None

    # use_default_tools=True -> default tools loaded
    await client.generate_image(
        prompt="draw a cat",
        model="models/gemini-3.1-flash-image-preview",
        google_search=False,
        image_search=False,
        use_default_tools=True,
    )
    assert captured_tools[-1] == [[None, None, None, [None, [[], []]]]]


def test_chat_service_does_not_guess_thought_for_multi_parts():
    """Verify model multi-part messages do not force thought=True."""
    req = GeminiGenerateContentRequest(
        contents=[
            GeminiContent(
                role="model",
                parts=[
                    GeminiPart(text="Paragraph 1"),
                    GeminiPart(text="Paragraph 2"),
                    GeminiPart(text="Paragraph 3"),
                ],
            )
        ]
    )
    normalized = normalize_gemini_request(req, "gemini-3.5-flash")
    model_content = normalized.contents[0]
    assert len(model_content.parts) == 3
    assert not any(p.thought for p in model_content.parts)

    req_with_thought = GeminiGenerateContentRequest(
        contents=[
            GeminiContent(
                role="model",
                parts=[
                    GeminiPart(text="Thinking...", thought=True),
                    GeminiPart(text="Answer with sig", thoughtSignature="sig123"),
                    GeminiPart(text="Normal answer"),
                ],
            )
        ]
    )
    norm_thought = normalize_gemini_request(req_with_thought, "gemini-3.5-flash")
    assert norm_thought.contents[0].parts[0].thought is True
    assert norm_thought.contents[0].parts[1].thought is True
    assert norm_thought.contents[0].parts[2].thought is False
