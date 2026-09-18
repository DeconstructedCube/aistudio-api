"""Unit tests for error mapping, constant-time auth, and wire codec schema."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from aistudio_api.api.dependencies import require_api_key, require_web_auth
from aistudio_api.application.account_rotator import AccountStats
from aistudio_api.application.api_service_gemini import classify_gemini_error_payload
from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    AuthError,
    RequestError,
    SessionExpiredError,
    UsageLimitExceeded,
)
from aistudio_api.infrastructure.gateway.wire_codec import (
    AistudioWireCodec,
    modify_body,
)


def test_classify_gemini_error_payload():
    """Verify exception mapping to Gemini HTTP status and reason code."""
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


def test_account_stats_short_cooldown_sync():
    """Verify short 60s cooldown is tracked accurately for monitoring."""
    stats = AccountStats(account_id="acc_test")
    stats.record_rate_limited(model="gemini-2.5-flash")

    assert not stats.is_available("gemini-2.5-flash")
    rem = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert 0 < rem <= 60.0

    stats.record_rate_limited(model="gemini-2.5-flash")
    assert not stats.is_available("gemini-2.5-flash")
    rem2 = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert 0 < rem2 <= 60.0

    stats.record_rate_limited(model="gemini-2.5-flash")
    assert not stats.is_available("gemini-2.5-flash")
    rem3 = stats.get_cooldown_remaining("gemini-2.5-flash")
    assert rem3 > 60.0


def test_dependencies_timing_safe_auth(monkeypatch):
    """Verify constant-time comparison and empty password guard."""
    monkeypatch.setattr(settings, "web_password", "super_secret_admin_pass")
    monkeypatch.setattr(settings, "api_keys", frozenset({"key123"}))

    # require_web_auth with correct password
    req_good = MagicMock(spec=Request)
    req_good.method = "POST"
    req_good.headers = {"authorization": "Bearer super_secret_admin_pass"}
    req_good.query_params = {}
    require_web_auth(req_good)

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
    require_api_key(req_admin_api)

    # Empty web_password guard
    monkeypatch.setattr(settings, "web_password", "")
    req_empty_pass = MagicMock(spec=Request)
    req_empty_pass.headers = {"x-api-key": ""}
    req_empty_pass.query_params = {}
    with pytest.raises(HTTPException):
        require_api_key(req_empty_pass)


def test_wire_codec_retains_schema_with_tools():
    """Verify response_schema and response_mime_type are retained when tools are present."""
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
