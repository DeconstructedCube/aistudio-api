"""辅助 RPC 的非法 HTTP 200 必须沿真实网关调用链报告传输错误。"""

from __future__ import annotations

import json
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.domain.errors import TransportError
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import (
    CREATE_DECISION_URL,
    SessionContext,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart

Endpoint = Literal["count_tokens", "create_decision"]


@pytest.fixture
def gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AIStudioClient, AsyncMock]:
    client = AIStudioClient.__new__(AIStudioClient)
    send_request = AsyncMock()
    session = MagicMock()
    session.ensure_ready = AsyncMock(return_value=SessionContext())
    session.send_request = send_request
    monkeypatch.setattr(client, "_session", session, raising=False)
    monkeypatch.setattr(client, "_build_upstream_headers", lambda _ctx: {})
    return client, send_request


async def _invoke(client: AIStudioClient, endpoint: Endpoint) -> dict[str, object]:
    if endpoint == "count_tokens":
        return await client.count_tokens(
            model="gemini-3.8-flash",
            contents=[AistudioContent(role="user", parts=[AistudioPart(text="hello")])],
        )
    prepared = PreparedRequest(
        url=CREATE_DECISION_URL,
        headers={},
        body="[]",
        model="models/gemini-decision-1",
        ctx=SessionContext(),
    )
    return await client.create_decision(prepared=prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["count_tokens", "create_decision"])
@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[7",
        b'{"error":{"code":503}}',
        b"[0]\n[",
    ],
    ids=["non-json", "truncated-json", "error-object", "truncated-second-line"],
)
async def test_malformed_http200_raises_transport_error(
    gateway: tuple[AIStudioClient, AsyncMock],
    endpoint: Endpoint,
    payload: bytes,
) -> None:
    client, send_request = gateway
    send_request.return_value = (200, payload)

    with pytest.raises(TransportError):
        await _invoke(client, endpoint)

    send_request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("count_tokens", b"[[12]]"),
        ("create_decision", b"[12]"),
        ("create_decision", b"[[],7]"),
    ],
    ids=["non-scalar-token-count", "non-array-answers", "non-array-usage"],
)
async def test_incompatible_rpc_array_raises_transport_error(
    gateway: tuple[AIStudioClient, AsyncMock],
    endpoint: Endpoint,
    payload: bytes,
) -> None:
    client, send_request = gateway
    send_request.return_value = (200, payload)

    with pytest.raises(TransportError):
        await _invoke(client, endpoint)

    send_request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"[0]", {"totalTokens": 0}),
        (b"[]", {"totalTokens": 0}),
        (b")]}'\n[0]", {"totalTokens": 0}),
        (b"[null,null,null,4]", {"totalTokens": 0, "cachedContentTokenCount": 4}),
        (
            b"[12,[],[[[2],1]],4,null,[[1,12]]]",
            {
                "totalTokens": 12,
                "cachedContentTokenCount": 4,
                "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 12}],
            },
        ),
    ],
    ids=["zero", "compact-zero", "xssi-zero", "cached-zero", "count-and-details"],
)
async def test_valid_count_tokens_http200_is_preserved(
    gateway: tuple[AIStudioClient, AsyncMock],
    payload: bytes,
    expected: dict[str, object],
) -> None:
    client, send_request = gateway
    send_request.return_value = (200, payload)

    assert await _invoke(client, "count_tokens") == expected
    send_request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", ")]}'\n"], ids=["plain", "xssi"])
async def test_valid_decision_http200_is_preserved(
    gateway: tuple[AIStudioClient, AsyncMock], prefix: str
) -> None:
    client, send_request = gateway
    response = [[["q", [0.9, 1, None, [1, 0.9]]]], [3]]
    send_request.return_value = (200, (prefix + json.dumps(response)).encode("utf-8"))

    assert await _invoke(client, "create_decision") == {
        "answers": {"q": {"type": "noul", "noul": 0.9, "confidence": 0.9}},
        "usage": {
            "total_input_tokens": 3,
            "input_tokens_by_modality": [],
            "total_output_tokens": 0,
            "total_tokens": 3,
        },
    }
    send_request.assert_awaited_once()
