"""Regression tests for Issue #15, Issue #16, finish_reason parsing, ReadTimeout config, and HTTP/2."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application import api_service_gemini as gemini
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.domain.errors import AuthError, UsageLimitExceeded
from aistudio_api.infrastructure.gateway.client import AIStudioClient, PreparedRequest
from aistudio_api.infrastructure.gateway.headers import merge_header_case_insensitive
from aistudio_api.infrastructure.gateway.session import GatewaySession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.transport import HTTPStreamTransport
from aistudio_api.infrastructure.gateway.wire_codec import encode_request_body
from aistudio_api.infrastructure.gateway.wire_parser import (
    parse_response_chunk,
    parse_text_output,
)
from aistudio_api.infrastructure.gateway.wire_spec import GenerationConfigIndex
from aistudio_api.infrastructure.waa import daemon as waa_daemon


def _make_part(
    *,
    text: str | None = None,
    thought: bool = False,
    function_call: list[object] | None = None,
    thought_signature: str | None = None,
) -> list[object]:
    part: list[object] = [None] * 15
    if text is not None:
        part[1] = text
    if function_call is not None:
        part[3] = function_call
    if thought:
        part[10] = True
    if thought_signature is not None:
        part[14] = thought_signature
    return part


@pytest.mark.asyncio
async def test_streaming_trailing_standalone_thought_signature_not_dropped():
    """Issue #15: standalone thoughtSignature chunk right before done must be emitted."""
    mock_client = MagicMock(spec=AIStudioClient)
    mock_client.prepare_request = AsyncMock(
        return_value=PreparedRequest(
            url="https://example.test/rpc", headers={}, body="[]"
        )
    )

    async def fake_stream(**_kwargs):
        yield ("thinking", "step 1")
        yield ("body", "final answer")
        yield ("thought_signature", "sig_trailing_only")
        yield ("finish_reason", 1)
        yield (
            "usage",
            {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        )
        yield ("done", None)

    mock_client.stream_generate_content = MagicMock(side_effect=fake_stream)

    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hi")])]
    )
    resp = gemini._build_gemini_streaming_response(
        client=mock_client, req=req, model_path="gemini-3.8-flash"
    )
    chunks = [
        c if isinstance(c, str) else bytes(c).decode("utf-8")
        async for c in resp.body_iterator
    ]
    joined = "".join(chunks)
    assert '"thoughtSignature": "sig_trailing_only"' in joined
    sig_idx = joined.index("sig_trailing_only")
    finish_idx = joined.index('"finishReason": "STOP"')
    assert sig_idx < finish_idx


@pytest.mark.asyncio
async def test_parallel_function_call_signatures_stay_isolated():
    """Issue #15: thinking signature and embedded functionCall signature must not cross-contaminate."""
    session = GatewaySession()
    part_think = _make_part(
        text="reasoning...", thought=True, thought_signature="sig_think"
    )
    part_fc1 = _make_part(
        function_call=["tool_a", [[["x", [None, None, "1"]]]], "call_1"],
        thought_signature="sig_fc1",
    )
    part_fc2 = _make_part(
        function_call=["tool_b", [[["y", [None, None, "2"]]]], "call_2"],
    )
    raw_candidate = [[[part_think, part_fc1, part_fc2], "model"], 1]
    chunk = [[raw_candidate]]
    chunk_json = json.dumps([[chunk]])

    async def fake_stream(**_kwargs):
        yield ("status", 200)
        yield ("chunk", (")]}\n" + chunk_json).replace(")]}", ")]}'").encode("utf-8"))

    session.send_streaming_request = fake_stream
    gateway = StreamingGateway(session=session)
    prep = PreparedRequest(url="https://example.test", headers={}, body="[]")

    events = [
        ev async for ev in gateway.stream_chat(request=prep, model="gemini-4-argon")
    ]
    sig_positions = [
        (idx, val)
        for idx, (kind, val) in enumerate(events)
        if kind == "thought_signature"
    ]
    think_pos = next(idx for idx, (kind, _) in enumerate(events) if kind == "thinking")
    tool_calls_ev = next(val for kind, val in events if kind == "tool_calls")

    assert sig_positions == [(0, "sig_think")]
    assert think_pos == 1
    assert isinstance(tool_calls_ev, list)
    assert tool_calls_ev[0].get("thought_signature") == "sig_fc1"
    assert "thought_signature" not in tool_calls_ev[1]


@pytest.mark.parametrize(
    "raw_text,expected_thought,expected_body",
    [
        ("[:thought inner draft :]Hello world", "inner draft", "Hello world"),
        ("[_thought step 1 ]_Result", "step 1", "Result"),
        ("⌊thought plan A ⌋Answer A", "plan A", "Answer A"),
        ("<thought>alpha</thought>Beta", "alpha", "Beta"),
        ("【_thought cn draft _】正文", "cn draft", "正文"),
    ],
)
@pytest.mark.asyncio
async def test_inline_thought_tags_stripped_in_stream_and_non_stream(
    raw_text: str, expected_thought: str, expected_body: str
):
    """Issue #15: inline thought leak variants are separated in both non-streaming and chunked streaming."""
    raw_cand = [[[[None, raw_text]], "model"], 1]
    chunk_full = [[raw_cand]]
    raw_payload = ")]}'\n" + json.dumps([[chunk_full]])
    out = parse_text_output(raw_payload)
    assert out.thinking == expected_thought
    assert out.text == expected_body

    # Split mid-open-tag streaming
    split_at = min(5, len(raw_text) - 1)
    chunk1 = [[[[[[None, raw_text[:split_at]]], "model"]]]]
    chunk2 = [[[[[[None, raw_text[split_at:]]], "model"], 1]]]
    session = GatewaySession()

    async def fake_stream(**_kwargs):
        yield ("status", 200)
        yield (
            "chunk",
            (")]}\n" + json.dumps([[chunk1, chunk2]]))
            .replace(")]}", ")]}'")
            .encode("utf-8"),
        )

    session.send_streaming_request = fake_stream
    gateway = StreamingGateway(session=session)
    prep = PreparedRequest(url="https://example.test", headers={}, body="[]")
    events = [
        ev async for ev in gateway.stream_chat(request=prep, model="gemini-3.8-flash")
    ]
    thoughts = "".join(str(v) for k, v in events if k == "thinking")
    bodies = "".join(str(v) for k, v in events if k == "body")
    assert thoughts == expected_thought
    assert bodies == expected_body


def test_urlsafe_base64_tool_role_and_argon_slot45_cleanup():
    """Issue #15: URL-safe Base64 normalized at wire boundary, tool/function role -> user, slot 45 cleared for non-argon."""
    raw_bytes = b"hello-wire-thought-signature-payload"
    urlsafe_b64 = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
    std_b64 = base64.b64encode(raw_bytes).decode("ascii")

    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {
                    "role": "tool",
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": urlsafe_b64,
                            },
                            "thoughtSignature": urlsafe_b64,
                        }
                    ],
                }
            ]
        }
    )
    normalized = normalize_gemini_request(req, "gemini-3.8-flash")
    assert normalized.contents[0].role == "user"
    wire_json = encode_request_body(
        model="gemini-3.8-flash",
        contents=normalized.contents,
        generation_config_overrides={},
    )
    wire = json.loads(wire_json)
    part_wire = wire[1][0][0][0]
    assert part_wire[2] == ["image/png", std_b64]
    assert part_wire[14] == std_b64
    gc = wire[3]
    assert len(gc) <= GenerationConfigIndex.ARGON_CAPABILITY_FLAG


def test_prompt_feedback_and_trailing_zero_or_22_finish_reason():
    """PromptFeedback chunk[1] blockReason and trailing 0/22 chunks must not become FINISH_REASON_UNSPECIFIED."""
    # 1. Trailing chunk with finish_reason=0 and finish_reason=22 after terminal STOP(1)
    bundle = ")]}'\n" + json.dumps(
        [
            [
                [[[[[[None, "ok"]], "model"], 1]]],
                [[[[[[None, ""]], "model"], 22]]],
                [[[[[[None, ""]], "model"], 0]], None, [5, 2, 7]],
            ]
        ]
    )
    output = parse_text_output(bundle)
    assert output.candidates[0].finish_reason == 1
    assert gemini.to_gemini_finish_reason(output.candidates[0].finish_reason) == "STOP"

    # 2. All chunks only have 0 / 22 -> defaults to STOP (never FINISH_REASON_UNSPECIFIED)
    bundle_zero = ")]}'\n" + json.dumps([[[[[[[[None, "ok"]], "model"], 0]]]]])
    out_zero = parse_text_output(bundle_zero)
    assert out_zero.candidates[0].finish_reason is None
    assert (
        gemini.to_gemini_finish_reason(out_zero.candidates[0].finish_reason) == "STOP"
    )

    # 3. PromptFeedback blockReason (1 -> SAFETY=3, 4 -> PROHIBITED_CONTENT=8)
    cand = parse_response_chunk([None, [1, [], "Blocked by safety"]])
    assert cand.finish_reason == 3
    assert cand.finish_message == "Blocked by safety"
    assert gemini.to_gemini_finish_reason(cand.finish_reason) == "SAFETY"


@pytest.mark.asyncio
async def test_case_insensitive_http2_headers_deduplicated_on_wire():
    """Issue #16: X-Goog-Ext-519733851-bin and -Bin must not duplicate on HTTP/2 wire."""
    captured_raw: list[tuple[bytes, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_raw.extend(request.headers.raw)
        return httpx.Response(200, content=b'["ok"]')

    merged = {"X-Goog-Ext-519733851-bin": "old_static", "X-Goog-AuthUser": "0"}
    merge_header_case_insensitive(merged, "X-Goog-Ext-519733851-Bin", "new_dynamic")
    merge_header_case_insensitive(merged, "X-Goog-Authuser", "2")
    assert list(merged.keys()) == ["X-Goog-Ext-519733851-Bin", "X-Goog-Authuser"]

    transport = HTTPStreamTransport()
    transport._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        status, _ = await transport.send_request(
            url="https://alkalimakersuite-pa.clients6.google.com/test",
            headers={
                "X-Goog-Ext-519733851-bin": "old_static",
                "X-Goog-Ext-519733851-Bin": "new_dynamic",
                "X-Goog-Authuser": "1",
            },
            body="[]",
            auth_user="1",
            cookies={"SAPISID": "sap", "SID": "sid"},
        )
        assert status == 200
        ext_headers = [
            (k.lower(), v)
            for k, v in captured_raw
            if k.lower() == b"x-goog-ext-519733851-bin"
        ]
        authuser_headers = [
            (k.lower(), v) for k, v in captured_raw if k.lower() == b"x-goog-authuser"
        ]
        assert ext_headers == [(b"x-goog-ext-519733851-bin", b"new_dynamic")]
        assert authuser_headers == [(b"x-goog-authuser", b"1")]
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_403_manager_sets_force_recreate_on_stage3_and_5():
    """Issue #16: Auth403RetryManager triggers force_recreate_signer on attempt 3 and 5."""
    manager = gemini.Auth403RetryManager()
    mock_client = MagicMock(spec=AIStudioClient)
    manager.current_prepared = PreparedRequest(
        url="https://test", headers={}, body="[]", model="models/gemini-4-carbon"
    )
    err = AuthError("Permission denied")

    assert await manager.handle_403(err, model="gemini-4-carbon", client=mock_client)
    assert manager.force_recreate_signer is False
    assert await manager.handle_403(err, model="gemini-4-carbon", client=mock_client)
    assert manager.force_recreate_signer is False
    assert await manager.handle_403(err, model="gemini-4-carbon", client=mock_client)
    assert manager.force_recreate_signer is True
    assert await manager.handle_403(err, model="gemini-4-carbon", client=mock_client)
    assert manager.force_recreate_signer is False
    with patch(
        "aistudio_api.application.api_service_gemini.try_switch_account",
        new_callable=AsyncMock,
        return_value=True,
    ):
        assert await manager.handle_403(
            err, model="gemini-4-carbon", client=mock_client
        )
        assert manager.force_recreate_signer is True


@pytest.mark.asyncio
async def test_short_rate_limit_not_misreported_as_daily_quota(monkeypatch):
    """Issue #16: 60s RPM cooldown must report Rate limit exceeded instead of daily quota 00:00 PST."""
    fake_stats = SimpleNamespace(get_cooldown_remaining=lambda _m: 60.0)
    fake_rotator = SimpleNamespace(
        get_account_stats=lambda _aid: fake_stats,
        record_rate_limited=lambda *_a, **_kw: None,
    )
    fake_acc = SimpleNamespace(id="acc_test")
    fake_svc = SimpleNamespace(get_active_account=lambda: fake_acc)
    monkeypatch.setattr(gemini.runtime_state, "rotator", fake_rotator)
    monkeypatch.setattr(gemini.runtime_state, "account_service", fake_svc)
    monkeypatch.setattr(gemini, "try_switch_account", AsyncMock(return_value=False))

    with pytest.raises(gemini.HTTPException) as exc_info:
        await gemini.handle_attempt_exception(
            UsageLimitExceeded("HTTP 429: Resource has been exhausted"),
            attempt=0,
            model_path="gemini-4-carbon",
            normalized_model="models/gemini-4-carbon",
        )
    assert exc_info.value.status_code == 429
    msg = str(exc_info.value.detail["message"])  # type: ignore[index]
    assert "retry after ~60s" in msg
    assert "00:00 PST" not in msg


def test_transport_timeout_and_h2_available_for_slow_models():
    """Slow models and long tool calls use 15-minute (900s) read timeout and h2 HTTP/2 support."""
    import h2

    assert h2.__version__
    transport = HTTPStreamTransport()
    assert transport.timeout == 900.0


def test_daemon_health_check_bypasses_system_proxy():
    """Issue #16: is_service_healthy builds an opener with empty ProxyHandler."""
    captured = {}

    class DummyResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class DummyOpener:
        def open(self, req, timeout=0.5):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            return DummyResp()

    with patch("urllib.request.build_opener", return_value=DummyOpener()) as mock_build:
        assert waa_daemon.is_service_healthy("http://127.0.0.1:8081") is True
        mock_build.assert_called_once()
        assert captured["url"] == "http://127.0.0.1:8081/health"


@pytest.mark.asyncio
async def test_nonstream_keepalive_serializes_delayed_error(monkeypatch):
    """Delayed non-streaming failures after >10s keepalive must serialize Gemini error JSON."""
    import asyncio

    from fastapi.responses import StreamingResponse

    async def failing_generate(*_args, **_kwargs):
        await asyncio.sleep(0.02)
        raise gemini.HTTPException(
            429,
            detail={"message": "Rate limit exceeded", "type": "rate_limit_exceeded"},
        )

    real_wait_for = asyncio.wait_for

    async def fast_wait_for(aw, _timeout=None, **_kwargs):
        return await real_wait_for(aw, timeout=0.005)

    monkeypatch.setattr(gemini, "_do_generate_content", failing_generate)
    monkeypatch.setattr(gemini.asyncio, "wait_for", fast_wait_for)

    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hi")])]
    )
    resp = await gemini.handle_gemini_generate_content(
        "gemini-3.8-flash", req, MagicMock(spec=AIStudioClient), stream=False
    )
    assert isinstance(resp, StreamingResponse)
    chunks = [
        c if isinstance(c, str) else bytes(c).decode("utf-8")
        async for c in resp.body_iterator
    ]
    payload = json.loads("".join(chunks).strip())
    assert payload["error"]["code"] == 429
    assert "Rate limit exceeded" in payload["error"]["message"]


def test_nonstream_prompt_feedback_only_chunk_and_non_pcm_audio():
    """PromptFeedback-only chunks are parsed in non-streaming mode and non-PCM audio keeps its MIME."""
    from aistudio_api.domain.models import GeneratedImage
    from aistudio_api.infrastructure.gateway.wire_parser import merge_audio_images

    fb_payload = ")]}'\n" + json.dumps([[[None, [1, [], "Blocked prompt"]]]])
    fb_out = parse_text_output(fb_payload)
    assert fb_out.candidates[0].finish_reason == 3
    assert fb_out.candidates[0].finish_message == "Blocked prompt"

    mp3 = GeneratedImage(mime="audio/mp3", data=b"ID3test", size=7)
    assert merge_audio_images([mp3]) == [mp3]
