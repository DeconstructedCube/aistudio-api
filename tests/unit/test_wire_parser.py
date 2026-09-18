"""Unit tests for wire_parser module."""

from __future__ import annotations

from aistudio_api.infrastructure.gateway.wire_parser import (
    _coerce_int,
    _decode_wire_argument_pairs,
    _decode_wire_value,
    _iter_response_chunks,
    parse_chunk_usage,
    parse_image_output,
    parse_response_chunk,
    parse_response_part,
    parse_text_output,
    parse_usage_metadata,
)


def test_coerce_int():
    assert _coerce_int(True) is None
    assert _coerce_int(False) is None
    assert _coerce_int(42) == 42
    assert _coerce_int(42.0) == 42
    assert _coerce_int(42.5) is None
    assert _coerce_int("123") == 123
    assert _coerce_int("-123") == -123
    assert _coerce_int("+123") == 123
    assert _coerce_int("abc") is None
    assert _coerce_int(None) is None


def test_decode_wire_value_and_pairs():
    # Value with >= 3 items where item[2] is present
    val = ["skip", "skip", "actual_value"]
    assert _decode_wire_value(val) == "actual_value"

    # Argument pairs
    pairs = [["key1", "val1"], ["key2", "val2"]]
    decoded = _decode_wire_argument_pairs(pairs)
    assert decoded == {"key1": "val1", "key2": "val2"}


def test_iter_response_chunks():
    assert _iter_response_chunks([]) == []
    assert _iter_response_chunks(None) == []

    chunk1 = [["candidate1"]]
    chunk2 = [["candidate2"]]
    top_level = [chunk1, chunk2]
    assert _iter_response_chunks(top_level) == [chunk1, chunk2]

    nested = [[chunk1, chunk2]]
    assert _iter_response_chunks(nested) == [chunk1, chunk2]


def test_parse_usage_metadata():
    assert parse_usage_metadata(None) == {}
    assert parse_usage_metadata("invalid") == {}

    raw = [10, 20, 30, 5, {"prompt": "details"}, None, None, None, None, 15]
    parsed = parse_usage_metadata(raw)
    assert parsed["prompt_tokens"] == 10
    assert parsed["completion_tokens"] == 35  # visible (20) + reasoning (15)
    assert parsed["total_tokens"] == 30
    assert parsed["cached_tokens"] == 5
    details = parsed.get("completion_tokens_details")
    assert isinstance(details, dict)
    assert details.get("reasoning_tokens") == 15
    assert details.get("visible_tokens") == 20

    # parse_chunk_usage
    chunk = ["model", None, raw]
    assert parse_chunk_usage(chunk) == parsed
    assert parse_chunk_usage("not_a_list") == {}


def test_parse_response_part():
    assert parse_response_part("not_a_list").text == ""

    # Part with reasoning/thinking flag at index 10
    part_with_thought = [
        None,
        "thinking text",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        True,
    ]
    parsed = parse_response_part(part_with_thought)
    assert parsed.text == "thinking text"
    assert parsed.thought is True

    # Part with thought at index 12 (AI Studio variant)
    part_variant = [
        None,
        "reasoning variant",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        1,
    ]
    parsed_variant = parse_response_part(part_variant)
    assert parsed_variant.text == "reasoning variant"
    assert parsed_variant.thought is True

    # Part with code execution
    part_code = [
        None,
        "",
        None,
        None,
        None,
        None,
        None,
        None,
        "print('hello')",
        "output: hello",
    ]
    parsed_code = parse_response_part(part_code)
    assert parsed_code.executable_code == "print('hello')"
    assert parsed_code.code_execution_result == "output: hello"


def test_parse_response_chunk_empty():
    assert parse_response_chunk([]).text == ""
    assert parse_response_chunk([[]]).text == ""
    assert parse_response_chunk([["not_a_list"]]).text == ""


def test_parse_text_output_fallback():
    output = parse_text_output("invalid json")
    assert output.text == ""
    assert output.candidates == []

    # parse_image_output forwards to parse_text_output
    img_output = parse_image_output("invalid json")
    assert img_output.text == ""
