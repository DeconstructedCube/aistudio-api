"""验证关闭公开 SSE 时真实客户端链路已等待 HTTP 响应释放。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import httpx
import pytest

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.session import SessionContext
from aistudio_api.infrastructure.gateway.wire_spec import PartIndex


@pytest.mark.asyncio
async def test_sse_close_awaits_http_response_through_real_gateway(monkeypatch):
    closed = asyncio.Event()
    pending = asyncio.Event()
    part: list[object] = [None] * (PartIndex.TEXT + 1)
    part[PartIndex.TEXT] = "synthetic_partial"
    candidate = [[[part], "model"], 1]
    payload = (")]}'\n" + json.dumps([[[[candidate]]]])).encode("utf-8")

    class ObservedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload
            await pending.wait()

        async def aclose(self):
            # 断言必须等异步释放完成，不能只安排下一轮事件循环中的清理。
            await asyncio.sleep(0)
            closed.set()

    http_response = httpx.Response(200, stream=ObservedStream())

    async def send(_request: httpx.Request) -> httpx.Response:
        return http_response

    client = AIStudioClient()
    context = SessionContext(cookies={"SID": "synthetic_sid"})
    client.session.set_ready_context(context)
    monkeypatch.setattr(
        client,
        "prepare_request",
        AsyncMock(
            return_value=PreparedRequest(
                url="https://example.test/rpc", headers={}, body="[]", ctx=context
            )
        ),
    )
    monkeypatch.setattr(gemini, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(gemini.runtime_state, "account_service", None)
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "虚构输入"}]}]}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(send), trust_env=False
    ) as http_client:
        monkeypatch.setattr(client.session._transport, "_client", http_client)
        response = gemini._build_gemini_streaming_response(
            client=client, req=req, model_path="gemini-3.8-flash"
        )
        iterator = response.body_iterator
        assert isinstance(iterator, AsyncGenerator)
        try:
            first = await iterator.__anext__()
            assert "synthetic_partial" in str(first)
            await iterator.aclose()
            assert closed.is_set()
            assert http_response.is_closed
        finally:
            # 失败基线也显式释放响应，避免测试自身依赖异步垃圾回收。
            await iterator.aclose()
            await http_response.aclose()
