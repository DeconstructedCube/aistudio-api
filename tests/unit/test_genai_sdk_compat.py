"""E2E and compatibility tests using the official google-genai SDK."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google import genai
from google.genai import errors, types

from aistudio_api.api.app import app
from aistudio_api.api.dependencies import get_client
from aistudio_api.domain.errors import UsageLimitExceeded
from aistudio_api.domain.models import Candidate, ModelOutput


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock()


@pytest.mark.asyncio
async def test_genai_sdk_aio_non_streaming(mock_client: MagicMock) -> None:
    """Test async non-streaming generation with official Google GenAI SDK."""
    mock_client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[Candidate(text="Hello from AI Studio!")],
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        )
    )

    app.dependency_overrides[get_client] = lambda: mock_client

    try:
        sdk_client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(
                base_url="http://testserver",
            ),
        )
        async with sdk_client.aio as aio_client:
            transport_obj = httpx.ASGITransport(app=app)
            aio_client._api_client._async_httpx_client = httpx.AsyncClient(
                transport=transport_obj,
                base_url="http://testserver",
            )

            response = await aio_client.models.generate_content(
                model="gemini-3.8-flash",
                contents="Hi",
            )

            assert response.text == "Hello from AI Studio!"
            assert response.candidates is not None
            assert len(response.candidates) == 1
            candidate = response.candidates[0]
            assert candidate.index == 0
            assert candidate.finish_reason == types.FinishReason.STOP
            assert response.usage_metadata is not None
            assert response.usage_metadata.prompt_token_count == 10
            assert response.usage_metadata.candidates_token_count == 20
            assert response.usage_metadata.total_token_count == 30
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_genai_sdk_aio_streaming(mock_client: MagicMock) -> None:
    """Test async streaming generation with official Google GenAI SDK."""

    async def fake_stream(
        *_args: object, **_kwargs: object
    ) -> AsyncGenerator[tuple[str, object], None]:
        yield ("thinking", "Thinking about the greeting...")
        yield ("body", "Hello ")
        yield ("body", "World!")
        yield (
            "usage",
            {
                "prompt_tokens": 5,
                "completion_tokens": 15,
                "total_tokens": 20,
            },
        )

    mock_client.stream_generate_content = fake_stream
    app.dependency_overrides[get_client] = lambda: mock_client

    try:
        sdk_client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(
                base_url="http://testserver",
            ),
        )
        async with sdk_client.aio as aio_client:
            transport_obj = httpx.ASGITransport(app=app)
            aio_client._api_client._async_httpx_client = httpx.AsyncClient(
                transport=transport_obj,
                base_url="http://testserver",
            )

            chunks = []
            async for chunk in await aio_client.models.generate_content_stream(
                model="gemini-3.8-flash",
                contents="Hi",
            ):
                chunks.append(chunk)

            assert len(chunks) == 4

            # Chunk 0: thinking
            assert chunks[0].candidates is not None
            cand0 = chunks[0].candidates[0]
            assert cand0.content is not None
            assert cand0.content.parts is not None
            assert cand0.content.parts[0].thought is True
            assert cand0.content.parts[0].text == "Thinking about the greeting..."
            assert cand0.index == 0

            # Chunk 1: text part 1
            assert chunks[1].text == "Hello "
            assert chunks[1].candidates is not None
            assert chunks[1].candidates[0].index == 0

            # Chunk 2: text part 2
            assert chunks[2].text == "World!"
            assert chunks[2].candidates is not None
            assert chunks[2].candidates[0].index == 0

            # Chunk 3: usage + STOP finishReason
            assert chunks[3].candidates is not None
            assert chunks[3].candidates[0].finish_reason == types.FinishReason.STOP
            assert chunks[3].usage_metadata is not None
            assert chunks[3].usage_metadata.total_token_count == 20
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_genai_sdk_function_calling_and_response_flow(
    mock_client: MagicMock,
) -> None:
    """Test full multi-turn function calling flow with Google GenAI SDK."""
    captured_requests: list[object] = []

    async def fake_generate(
        model: str,
        contents: list[object],
        **kwargs: object,
    ) -> ModelOutput:
        captured_requests.append((model, contents))
        if len(captured_requests) == 1:
            return ModelOutput(
                candidates=[
                    Candidate(
                        text="",
                        function_calls=[
                            {
                                "name": "get_current_weather",
                                "args": {"location": "San Francisco"},
                            }
                        ],
                    )
                ],
                usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            )
        return ModelOutput(
            candidates=[
                Candidate(
                    text="The current weather in San Francisco is sunny and 18°C."
                )
            ],
            usage={"prompt_tokens": 25, "completion_tokens": 15, "total_tokens": 40},
        )

    mock_client.generate_content = AsyncMock(side_effect=fake_generate)
    app.dependency_overrides[get_client] = lambda: mock_client

    try:
        sdk_client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(base_url="http://testserver"),
        )
        async with sdk_client.aio as aio_client:
            aio_client._api_client._async_httpx_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )

            # Turn 1: user asks question, model returns function call
            turn1_resp = await aio_client.models.generate_content(
                model="gemini-3.8-flash",
                contents="What is the weather in San Francisco?",
            )
            assert turn1_resp.function_calls is not None
            assert len(turn1_resp.function_calls) == 1
            assert turn1_resp.function_calls[0].name == "get_current_weather"
            assert turn1_resp.function_calls[0].args == {"location": "San Francisco"}

            # Turn 2: user supplies function execution result, model answers
            turn2_contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text="What is the weather in San Francisco?"
                        )
                    ],
                ),
                types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name="get_current_weather",
                            args={"location": "San Francisco"},
                        )
                    ],
                ),
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name="get_current_weather",
                            response={"temp": "18C", "condition": "sunny"},
                        )
                    ],
                ),
            ]
            turn2_resp = await aio_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=turn2_contents,
            )
            assert (
                turn2_resp.text
                == "The current weather in San Francisco is sunny and 18°C."
            )
            assert len(captured_requests) == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_genai_sdk_standard_api_error_parsing(mock_client: MagicMock) -> None:
    """Verify google-genai SDK APIError parses standard Google error format."""
    mock_client.generate_content = AsyncMock(
        side_effect=UsageLimitExceeded("Daily quota exhausted for model")
    )
    app.dependency_overrides[get_client] = lambda: mock_client

    try:
        sdk_client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(base_url="http://testserver"),
        )
        async with sdk_client.aio as aio_client:
            aio_client._api_client._async_httpx_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            with pytest.raises(errors.APIError) as exc_info:
                await aio_client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents="Hello",
                )
            err = exc_info.value
            assert err.code == 429
            assert err.status == "RESOURCE_EXHAUSTED"
            assert "daily quota" in str(err.message).lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_genai_sdk_tool_config_forwarding(mock_client: MagicMock) -> None:
    """Verify google-genai SDK tool_config (mode=ANY) is parsed and forwarded."""
    mock_client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[Candidate(text="Called")],
            usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )
    )
    app.dependency_overrides[get_client] = lambda: mock_client

    try:
        sdk_client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(base_url="http://testserver"),
        )
        async with sdk_client.aio as aio_client:
            aio_client._api_client._async_httpx_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            await aio_client.models.generate_content(
                model="gemini-3.8-flash",
                contents="What is the weather?",
                config=types.GenerateContentConfig(
                    tools=[
                        types.Tool(
                            function_declarations=[
                                types.FunctionDeclaration(
                                    name="get_weather",
                                    description="Get weather",
                                    parameters=types.Schema(
                                        type=types.Type.OBJECT,
                                        properties={
                                            "city": types.Schema(type=types.Type.STRING)
                                        },
                                    ),
                                )
                            ]
                        )
                    ],
                    tool_config=types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.ANY,
                        )
                    ),
                ),
            )
            assert mock_client.generate_content.called
            call_kwargs = mock_client.generate_content.call_args.kwargs
            assert "tool_config" in call_kwargs
            assert call_kwargs["tool_config"] == [None, [2]]
    finally:
        app.dependency_overrides.clear()
