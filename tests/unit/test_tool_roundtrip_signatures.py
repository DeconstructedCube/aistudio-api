"""验证客户端原样回传工具回复时，签名、调用标识与结果仍能到达上游。"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.dependencies import get_client
from aistudio_api.api.responses import to_gemini_parts
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as gemini_service
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.domain.models import CandidatePart
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import GatewaySession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.wire_codec import encode_request_body
from aistudio_api.infrastructure.gateway.wire_parser import parse_response_part
from aistudio_api.infrastructure.gateway.wire_spec import PartIndex, TopLevelIndex

CALL_SIGNATURE = base64.b64encode(b"provider-call-signature-fixture").decode("ascii")
SECOND_SIGNATURE = base64.b64encode(b"provider-second-call-signature").decode("ascii")
TRAILING_SIGNATURE = base64.b64encode(b"provider-trailing-signature-fixture").decode(
    "ascii"
)


def _replay_model_turn(
    model_parts: list[dict[str, object]],
    tool_results: list[dict[str, object]] | None = None,
) -> list[tuple[list[object], str]]:
    request = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {"role": "user", "parts": [{"text": "查询天气并继续回答。"}]},
                {"role": "model", "parts": model_parts},
                {
                    "role": "user",
                    "parts": tool_results or [{"text": "请继续。"}],
                },
            ]
        }
    )
    normalized = normalize_gemini_request(request, "gemini-4-argon")
    body = json.loads(
        encode_request_body(
            model=normalized.model,
            contents=normalized.contents,
            tools=normalized.tools,
            tool_config=normalized.tool_config,
        )
    )
    raw_contents = body[TopLevelIndex.CONTENTS]
    assert isinstance(raw_contents, list)
    contents: list[tuple[list[object], str]] = []
    for content in raw_contents:
        assert isinstance(content, list) and len(content) == 2
        parts, role = content
        assert isinstance(parts, list)
        assert isinstance(role, str)
        contents.append((parts, role))
    return contents


@pytest.mark.parametrize("with_function_call", [False, True])
def test_generated_standalone_signature_survives_next_turn(
    with_function_call: bool,
) -> None:
    candidate_parts: list[CandidatePart] = (
        [
            CandidatePart(
                kind="function_call",
                payload={
                    "name": "getWeather",
                    "args": {"city": "Shanghai"},
                    "call_id": "call_weather",
                    "thought_signature": CALL_SIGNATURE,
                },
            )
        ]
        if with_function_call
        else []
    )
    candidate_parts.append(
        CandidatePart(
            kind="standalone_signature",
            thought_signature=TRAILING_SIGNATURE,
        )
    )
    returned_parts = [
        part.model_dump(mode="json", exclude_none=True)
        for part in to_gemini_parts(candidate_parts)
    ]
    wire_contents = _replay_model_turn(returned_parts)
    model_wire, role = wire_contents[1]

    assert role == "model"
    assert len(model_wire) == len(returned_parts)
    trailing = parse_response_part(model_wire[-1])
    assert trailing.thought_signature == TRAILING_SIGNATURE
    assert trailing.text == ""
    assert trailing.function_call is None
    if with_function_call:
        call = parse_response_part(model_wire[0])
        assert call.thought_signature == CALL_SIGNATURE
        assert call.function_call is not None
        assert call.function_call["call_id"] == "call_weather"


def test_parallel_function_calls_keep_ids_signatures_and_matching_results() -> None:
    calls: list[dict[str, object]] = [
        {
            "name": "getWeather",
            "args": {"city": "Shanghai", "options": {"days": [1, 2]}},
            "call_id": "call_weather",
            "thought_signature": CALL_SIGNATURE,
        },
        {
            "name": "getTime",
            "args": {"city": "Shanghai"},
            "call_id": "call_time",
            "thought_signature": SECOND_SIGNATURE,
        },
    ]
    returned_parts = [
        part.model_dump(mode="json", exclude_none=True)
        for part in to_gemini_parts(
            [CandidatePart(kind="function_call", payload=call) for call in calls]
        )
    ]
    results: list[dict[str, object]] = [
        {
            "functionResponse": {
                "name": "getWeather",
                "id": "call_weather",
                "response": {"forecast": ["晴", "阴"]},
            }
        },
        {
            "functionResponse": {
                "name": "getTime",
                "id": "call_time",
                "response": {"hour": 8},
            }
        },
    ]
    wire_contents = _replay_model_turn(returned_parts, results)
    model_wire, role = wire_contents[1]
    result_wire, result_role = wire_contents[2]

    assert role == "model"
    assert result_role == "user"
    assert len(model_wire) == len(calls)
    assert len(result_wire) == len(results)
    for raw_part, expected in zip(model_wire, calls, strict=True):
        parsed = parse_response_part(raw_part)
        assert parsed.function_call is not None
        assert parsed.function_call["name"] == expected["name"]
        assert parsed.function_call["args"] == expected["args"]
        assert parsed.function_call["call_id"] == expected["call_id"]
        assert parsed.thought_signature == expected["thought_signature"]
    for raw_part, expected in zip(result_wire, results, strict=True):
        parsed = parse_response_part(raw_part).function_response
        assert parsed is not None
        result = expected["functionResponse"]
        assert isinstance(result, dict)
        assert parsed["name"] == result["name"]
        assert parsed["call_id"] == result["id"]
        assert parsed["args"] == result["response"]


@pytest.mark.asyncio
async def test_streaming_tool_and_tail_signature_survive_full_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 沿用真实 AI Studio 函数调用报文形状，经过网关解析和公开 SSE 路由。
    raw_call: list[object] = [None] * (PartIndex.THOUGHT_SIGNATURE + 1)
    raw_call[PartIndex.FUNCTION_CALL_ALT] = [
        "getWeather",
        [[["city", [None, None, "Shanghai"]]]],
        "e6ni61kr",
    ]
    raw_call[PartIndex.THOUGHT_SIGNATURE] = CALL_SIGNATURE
    raw_tail: list[object] = [None] * (PartIndex.THOUGHT_SIGNATURE + 1)
    raw_tail[PartIndex.THOUGHT_SIGNATURE] = TRAILING_SIGNATURE
    raw_candidate = [[[raw_call, raw_tail], "model"], 1]
    wire_reply = ")]}'\n" + json.dumps([[[[raw_candidate]]]])

    async def send_streaming_request(**_kwargs):
        yield ("status", 200)
        yield ("chunk", wire_reply.encode("utf-8"))

    session = GatewaySession()
    session.send_streaming_request = send_streaming_request
    gateway = StreamingGateway(session=session)
    client = MagicMock(spec=AIStudioClient)
    client.prepare_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/rpc", headers={}, body="[]"
        )
    )

    async def stream_generate_content(*, model, prepared):
        async for event in gateway.stream_chat(request=prepared, model=model):
            yield event

    client.stream_generate_content = stream_generate_content
    # 账号门禁与协议验收分开；报文解析、SSE 和回传线编码均使用真实实现。
    monkeypatch.setattr(gemini_service, "ensure_active_account", AsyncMock())
    monkeypatch.setitem(app.dependency_overrides, get_client, lambda: client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http_client:
        response = await http_client.post(
            "/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse",
            json={"contents": [{"role": "user", "parts": [{"text": "查询天气。"}]}]},
        )

    assert response.status_code == 200
    returned_parts: list[dict[str, object]] = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        assert "error" not in payload
        for candidate in payload.get("candidates", []):
            returned_parts.extend(candidate.get("content", {}).get("parts", []))
    assert len(returned_parts) == 2
    returned_call = returned_parts[0]["functionCall"]
    assert isinstance(returned_call, dict)
    assert returned_call["id"] == "e6ni61kr"
    assert returned_parts[0]["thoughtSignature"] == CALL_SIGNATURE
    assert returned_parts[1] == {"thoughtSignature": TRAILING_SIGNATURE}

    model_wire, role = _replay_model_turn(returned_parts)[1]
    assert role == "model"
    assert len(model_wire) == len(returned_parts)
    call = parse_response_part(model_wire[0])
    assert call.function_call is not None
    assert call.function_call["args"] == {"city": "Shanghai"}
    assert call.function_call["call_id"] == "e6ni61kr"
    assert call.thought_signature == CALL_SIGNATURE
    assert parse_response_part(model_wire[1]).thought_signature == TRAILING_SIGNATURE
