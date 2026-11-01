from aistudio_api.infrastructure.gateway.model_defaults import (
    ThinkingLevel,
    strip_model_thinking_suffix,
)
from aistudio_api.infrastructure.gateway.model_discovery import model_discovery
from aistudio_api.infrastructure.gateway.wire_types import (
    SKIP_THOUGHT_SIG_VALIDATOR_B64,
    AistudioContent,
    AistudioPart,
)
from aistudio_api.infrastructure.gateway.wire_spec import PartIndex
from aistudio_api.infrastructure.gateway.cookie_rotate import DEFAULT_USER_AGENT
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.api.schemas import GeminiGenerateContentRequest, GeminiContent, GeminiPart


def test_strip_model_thinking_suffix():
    clean, level = strip_model_thinking_suffix("gemini-3.8-flash-high")
    assert clean == "gemini-3.8-flash"
    assert level == ThinkingLevel.HIGH

    clean, level = strip_model_thinking_suffix("models/gemini-4-argon-minimal")
    assert clean == "models/gemini-4-argon"
    assert level == ThinkingLevel.MINIMAL

    clean, level = strip_model_thinking_suffix("gemini-3.5-flash-lite-medium")
    assert clean == "gemini-3.5-flash-lite"
    assert level == ThinkingLevel.MEDIUM

    clean, level = strip_model_thinking_suffix("gemini-3.8-flash-low")
    assert clean == "gemini-3.8-flash"
    assert level == ThinkingLevel.LOW

    clean, level = strip_model_thinking_suffix("gemini-3.8-flash")
    assert clean == "gemini-3.8-flash"
    assert level is None


def test_normalize_gemini_request_with_thinking_suffix():
    req = GeminiGenerateContentRequest(
        contents=[
            GeminiContent(
                role="user",
                parts=[GeminiPart(text="Hello")],
            )
        ]
    )
    normalized = normalize_gemini_request(req, "gemini-3.8-flash-high")
    assert normalized.model == "models/gemini-3.8-flash"
    # ThinkingLevel.HIGH is 3 in wire [1, None, None, 3]
    wire_thinking = normalized.generation_config_overrides["thinking_config"]
    assert wire_thinking == [1, None, None, 3]


def test_tool_calling_skip_thought_signature_validator():
    # Part with function call and no signature
    part_no_sig = AistudioPart(
        function_call=("get_weather", {"city": "Tokyo"}),
    )
    wire = part_no_sig.to_wire()
    assert wire[PartIndex.THOUGHT_SIGNATURE] == SKIP_THOUGHT_SIG_VALIDATOR_B64
    assert wire[PartIndex.THOUGHT_SIGNATURE] == "c2tpcF90aG91Z2h0X3NpZ25hdHVyZV92YWxpZGF0b3I="

    # Part with function call and existing signature
    part_with_sig = AistudioPart(
        function_call=("get_weather", {"city": "Paris"}),
        thought_signature="custom_signature_xyz",
    )
    wire2 = part_with_sig.to_wire()
    assert wire2[PartIndex.THOUGHT_SIGNATURE] == "custom_signature_xyz"


def test_cookie_rotate_user_agent_aligned():
    assert "Chrome/151.0.0.0" in DEFAULT_USER_AGENT


def test_model_discovery_thinking_suffix_paid_rule():
    assert model_discovery.is_paid_model("gemini-4-argon-high") is True
    tiers = model_discovery.get_required_tiers("gemini-4-argon-high")
    assert any(t in ("Pro", "Ultra", "Ultra_20X") for t in tiers)
