"""Tests for systematic bug fixes and architectural refactorings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from aistudio_api.api.dependencies import require_api_key, require_web_auth
from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application.account_rotator import AccountStats
from aistudio_api.application.api_service_gemini import (
    classify_gemini_error_payload,
)
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    AuthError,
    RequestError,
    SessionExpiredError,
    UsageLimitExceeded,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    STREAM_CLEANUP_JS,
    STREAMING_INIT_JS,
)
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport
from aistudio_api.infrastructure.gateway.wire_codec import (
    AistudioWireCodec,
    modify_body,
)


# 1. client.py生图工具设置修复测试
@pytest.mark.asyncio
async def test_generate_image_tools_selection():
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


# 2. session.py V8 内存泄漏清理脚本测试
def test_streaming_scripts_include_window_deletions():
    assert "delete window.__streams[rid]" in STREAMING_INIT_JS
    assert "delete window.__stream_next[rid]" in STREAMING_INIT_JS
    assert "delete window.__stream_abort[rid]" in STREAMING_INIT_JS

    assert "delete window.__streams[rid]" in STREAM_CLEANUP_JS
    assert "delete window.__stream_next[rid]" in STREAM_CLEANUP_JS
    assert "delete window.__stream_abort[rid]" in STREAM_CLEANUP_JS


# 3. session.py / transport.py 流式中途超时抛错测试 (不静默截断)
@pytest.mark.asyncio
async def test_transport_streaming_timeout_raises():
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)

    call_count = 0

    async def fake_evaluate(expr, args=None, *a, **kw):
        nonlocal call_count
        if "window.__stream_next[rid](250)" in expr:
            call_count += 1
            if call_count == 1:
                return {"type": "status", "status": 200}
            if call_count == 2:
                return {"type": "chunk", "text": "partial chunk"}
            return {"type": "idle"}
        return None

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    with pytest.raises(
        TimeoutError, match="streaming response timed out before completion"
    ):
        async for _ in transport.send_streaming_request(
            page,
            url="http://test.com",
            headers={},
            body="{}",
            timeout_ms=300,  # 0.3s timeout
        ):
            pass


# 4. account_rotator.py 冷却状态与监控数据一致性测试
def test_account_stats_short_cooldown_sync():
    stats = AccountStats(account_id="acc_test")
    stats.record_rate_limited(model="gemini-2.5-flash")

    # Limit count == 1: 60s cooldown
    assert not stats.is_available("gemini-2.5-flash")
    rem = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert 0 < rem <= 60.0

    # Record 2nd rate limit
    stats.record_rate_limited(model="gemini-2.5-flash")
    assert not stats.is_available("gemini-2.5-flash")
    rem2 = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert 0 < rem2 <= 60.0

    # Record 3rd rate limit: lock until Pacific midnight
    stats.record_rate_limited(model="gemini-2.5-flash")
    assert not stats.is_available("gemini-2.5-flash")
    rem3 = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert rem3 > 60.0


# 5. api_service_gemini.py 异常状态码对齐测试
def test_classify_gemini_error_payload():
    assert classify_gemini_error_payload(SessionExpiredError("expired")) == (
        401,
        "All accounts have expired sessions. Please import fresh cookies.",
        "UNAUTHENTICATED",
    )
    assert classify_gemini_error_payload(AuthError("auth forbidden")) == (
        403,
        "auth forbidden",
        "PERMISSION_DENIED",
    )
    assert classify_gemini_error_payload(UsageLimitExceeded("quota exceeded")) == (
        429,
        "quota exceeded",
        "RESOURCE_EXHAUSTED",
    )
    assert classify_gemini_error_payload(ValueError("invalid input")) == (
        400,
        "invalid input",
        "INVALID_ARGUMENT",
    )
    assert classify_gemini_error_payload(RequestError(429, "rate limit")) == (
        429,
        "HTTP 429: rate limit",
        "RESOURCE_EXHAUSTED",
    )


# 6. chat_service.py model 角色多 Part 不武断推断 thought 测试
def test_chat_service_does_not_guess_thought_for_multi_parts():
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
    # None of them should be forced to thought=True unless explicitly marked
    assert not any(p.thought for p in model_content.parts)

    # When thought is explicitly set or thoughtSignature is present
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


# 7. session.py 身份校验失败不物理删除 profile 目录
@pytest.mark.asyncio
async def test_verify_account_identity_does_not_rmtree(tmp_path):
    session = BrowserSession(port=9222)
    fake_meta_dir = tmp_path / "acc"
    fake_meta_dir.mkdir()
    fake_auth = fake_meta_dir / "auth.json"
    fake_auth.write_text("{}")
    fake_meta = fake_meta_dir / "meta.json"
    fake_meta.write_text('{"id": "acc_1", "email": "expected@gmail.com"}')
    fake_profile = fake_meta_dir / "profile"
    fake_profile.mkdir()
    (fake_profile / "data.txt").write_text("keep me")

    session._auth_file = str(fake_auth)
    session._profile_dir = str(fake_profile)

    page = MagicMock(spec=CDPPage)
    page.evaluate = AsyncMock(return_value=False)
    page.content = AsyncMock(return_value="<html>other page</html>")
    page.get_cookies = AsyncMock(return_value=[])

    with pytest.raises(RuntimeError, match="页面未登录期望的账号"):
        await session._verify_account_identity(page)

    # Profile directory must NOT be deleted!
    assert fake_profile.exists()
    assert (fake_profile / "data.txt").exists()


# 8. capture.py / session.py 按模型隔离模板测试
@pytest.mark.asyncio
async def test_session_template_capture_per_model():
    session = BrowserSession(port=9222)
    session._bootstrap_template = {
        "url": "http://bootstrap.com",
        "headers": {},
        "body": '["models/gemini-3.7-flash"]',
    }
    session.ensure_botguard_service = AsyncMock()  # type: ignore[method-assign]
    session._click_run_button = AsyncMock(return_value=True)  # type: ignore[method-assign]
    session._wait_until_idle = AsyncMock()  # type: ignore[method-assign]

    # gemini-3.7-flash uses bootstrap
    mock_page = MagicMock(spec=CDPPage)
    mock_page.evaluate = AsyncMock(return_value="")
    mock_page.fill = AsyncMock()

    def fake_on_request(cb):
        # Immediately trigger captured request when on_request is registered
        cb(
            {
                "url": "http://pro.com/GenerateContent",
                "headers": {"x-model": "pro"},
                "post_data": '["models/gemini-3.1-pro-preview",' + "x" * 150 + "]",
            }
        )
        return lambda: None

    mock_page.on_request = MagicMock(side_effect=fake_on_request)
    mock_page.on_response = MagicMock(return_value=lambda: None)
    mock_page.wait_for_timeout = AsyncMock()
    session.ensure_botguard_service = AsyncMock(return_value=mock_page)  # type: ignore[method-assign]

    result_tpl = await session.capture_template("gemini-3.1-pro-preview")
    assert result_tpl["url"] == "http://pro.com/GenerateContent"
    assert (
        session._templates["gemini-3.1-pro-preview"]["url"]
        == "http://pro.com/GenerateContent"
    )


def test_dependencies_timing_safe_auth(monkeypatch):
    monkeypatch.setattr(settings, "web_password", "super_secret_admin_pass")
    monkeypatch.setattr(settings, "api_keys", frozenset({"key123"}))

    # require_web_auth with correct password
    req_good = MagicMock(spec=Request)
    req_good.method = "POST"
    req_good.headers = {"authorization": "Bearer super_secret_admin_pass"}
    req_good.query_params = {}
    require_web_auth(req_good)  # Should not raise

    # require_web_auth with wrong password
    req_bad = MagicMock(spec=Request)
    req_bad.method = "POST"
    req_bad.headers = {"authorization": "Bearer wrong"}
    req_bad.query_params = {}
    with pytest.raises(HTTPException) as exc_info:
        require_web_auth(req_bad)
    assert exc_info.value.status_code == 401

    # require_api_key matching admin web_password
    req_admin_api = MagicMock(spec=Request)
    req_admin_api.headers = {"x-api-key": "super_secret_admin_pass"}
    req_admin_api.query_params = {}
    require_api_key(req_admin_api)  # Should not raise

    # Empty web_password guard
    monkeypatch.setattr(settings, "web_password", "")
    req_empty_pass = MagicMock(spec=Request)
    req_empty_pass.headers = {"x-api-key": ""}
    req_empty_pass.query_params = {}
    with pytest.raises(HTTPException):
        require_api_key(req_empty_pass)


# 11. wire_codec.py 保留同时声明 tools 与 responseSchema 测试
def test_wire_codec_retains_schema_with_tools():
    original = '["models/original",[[[[null,"old"]],"user"]],null,[null,null,null,128,0.5,0.8,16,"application/json",[6]],"!snap",null,null]'
    rewritten = modify_body(
        original,
        model="models/gemini-3.5-flash",
        prompt="test",
        tools=[[[]]],
        generation_config_overrides={
            "response_mime_type": "application/json",
            "response_schema": [6],
        },
        sanitize_plain_text=False,
    )
    decoded = AistudioWireCodec().decode(rewritten)
    assert decoded.tools == [[[]]]
    assert decoded.generation_config.response_mime_type == "application/json"
    assert decoded.generation_config.response_schema == [6]
