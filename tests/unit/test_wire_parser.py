"""Unit tests for wire_parser module."""

from __future__ import annotations

from pathlib import Path

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

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


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

    # Guard against content blocks [parts, role] or role string misinterpreted as text part
    content_block = [[[None, "actual content"]], "model"]
    parsed_cb = parse_response_part(content_block)
    assert parsed_cb.text == ""

    # Guard against 2-element role tuple
    role_part = [["nested"], "model"]
    assert parse_response_part(role_part).text == ""
    assert parse_response_part(["user", "user"]).text == ""


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


def test_parse_image_output_keeps_only_final_images_in_images_field():
    raw = (FIXTURES / "test_image_output.json").read_text()
    output = parse_image_output(raw)

    assert len(output.images) == 1
    assert len(output.reasoning_images) == 1
    assert output.images[0].mime == "image/jpeg"
    assert output.reasoning_images[0].mime == "image/jpeg"
    assert output.images[0].data != output.reasoning_images[0].data
    assert output.thinking.startswith("**Envisioning a Kitty Scene**")


def test_decode_wire_argument_pairs_with_three_element_array():
    from aistudio_api.infrastructure.gateway.wire_parser import (
        _decode_wire_argument_pairs,
    )

    raw = [["city", "San Francisco", "extra_field"]]
    decoded = _decode_wire_argument_pairs(raw)
    assert decoded == {"city": "San Francisco"}


def test_decode_wire_value_booleans_and_numbers():
    # Value booleans (JSPB boolean compression 0/1 and explicit bool)
    assert _decode_wire_value([None, None, None, 0]) is False
    assert _decode_wire_value([None, None, None, 1]) is True
    assert _decode_wire_value([None, None, None, False]) is False
    assert _decode_wire_value([None, None, None, True]) is True

    # Value numbers
    assert _decode_wire_value([None, 0]) == 0
    assert _decode_wire_value([None, 42]) == 42
    assert _decode_wire_value([None, 3.14]) == 3.14

    # Null value
    assert _decode_wire_value([0]) is None

    # Strings
    assert _decode_wire_value([None, None, "hello"]) == "hello"

    # Plain non-list
    assert _decode_wire_value("raw_str") == "raw_str"
    assert _decode_wire_value(123) == 123
    assert _decode_wire_value(True) is True


def test_decode_wire_list_single_string_preserves_array():
    from aistudio_api.infrastructure.gateway.wire_parser import (
        _decode_wire_struct,
    )

    # Value node containing a single string item inside repeated ListValue
    raw = [
        [
            "items",
            [
                None,
                None,
                None,
                None,
                None,
                [[None, None, "task1"]],
            ],
        ]
    ]
    decoded = _decode_wire_struct(raw)
    assert decoded == {"items": ["task1"]}


def test_decode_wire_list_single_struct_preserves_array():
    from aistudio_api.infrastructure.gateway.wire_parser import (
        _decode_wire_struct,
    )

    # Simulating ask tool: single question struct inside repeated ListValue
    raw = [
        [
            "questions",
            [
                None,
                None,
                None,
                None,
                None,
                [
                    [
                        [
                            None,
                            None,
                            None,
                            None,
                            [
                                ["id", [None, None, "storage"]],
                                ["question", [None, None, "Database?"]],
                                ["multi", [None, None, None, 0]],
                            ],
                        ]
                    ]
                ],
            ],
        ]
    ]
    decoded = _decode_wire_struct(raw)
    assert decoded == {
        "questions": [
            {
                "id": "storage",
                "question": "Database?",
                "multi": False,
            }
        ]
    }


def test_decode_wire_list_empty_list_returns_list_not_dict():
    from aistudio_api.infrastructure.gateway.wire_parser import (
        _decode_wire_struct,
    )

    raw = [
        [
            "empty_list",
            [
                None,
                None,
                None,
                None,
                None,
                [],
            ],
        ]
    ]
    decoded = _decode_wire_struct(raw)
    assert decoded == {"empty_list": []}
    assert isinstance(decoded["empty_list"], list)


def test_parse_response_chunk_with_ask_tool_payload():
    # Full chunk containing ask tool call with single question dict
    chunk: list[object] = [
        [
            [
                [
                    [
                        [
                            None,
                        None,
                        None,
                        [
                            "ask",
                            [
                                [
                                    "questions",
                                    [
                                        None,
                                        None,
                                        None,
                                        None,
                                        None,
                                        [
                                            [
                                                [
                                                    None,
                                                    None,
                                                    None,
                                                    None,
                                                    [
                                                        ["id", [None, None, "storage"]],
                                                        ["multi", [None, None, None, 0]],
                                                        ["recommended", [None, 0]],
                                                    ],
                                                ]
                                            ]
                                        ],
                                    ],
                                ]
                            ],
                            "call_ask_123",
                        ],
                        ]
                    ]
                ],
                "model",
            ],
            1,
        ]
    ]
    candidate = parse_response_chunk(chunk)
    assert len(candidate.function_calls) == 1
    fc = candidate.function_calls[0]
    assert fc["name"] == "ask"
    assert fc["call_id"] == "call_ask_123"
    args = fc["args"]
    assert isinstance(args, dict)
    assert args == {
        "questions": [
            {
                "id": "storage",
                "multi": False,
                "recommended": 0,
            }
        ]
    }
