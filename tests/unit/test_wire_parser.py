"""Unit tests for wire_parser module."""

from __future__ import annotations

from pathlib import Path

from aistudio_api.infrastructure.gateway.wire_parser import (
    _coerce_int,
    _decode_wire_struct,
    _decode_wire_value,
    _iter_response_chunks,
    parse_chunk_usage,
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


def test_decode_wire_value_and_struct():
    # Value with >= 3 items where item[2] is present
    val = ["skip", "skip", "actual_value"]
    assert _decode_wire_value(val) == "actual_value"

    # Struct pairs
    pairs = [["key1", "val1"], ["key2", "val2"]]
    decoded = _decode_wire_struct(pairs)
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
    assert parse_usage_metadata([None, None, None, None]) == {}
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
    assert parsed.function_call is None
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

    # Guard against 2-element role tuple, while preserving valid text parts whose text is a role word
    role_part = [["nested"], "model"]
    assert parse_response_part(role_part).text == ""
    assert parse_response_part(["user", "user"]).text == ""
    assert parse_response_part([None, "model"]).text == "model"
    assert parse_response_part([None, "user"]).text == "user"


def test_parse_response_chunk_empty():
    assert parse_response_chunk([]).text == ""
    assert parse_response_chunk([[]]).text == ""
    assert parse_response_chunk([["not_a_list"]]).text == ""


def test_parse_text_output_fallback():
    output = parse_text_output("invalid json")
    assert output.text == ""
    assert output.candidates == []


def test_parse_image_output_keeps_only_final_images_in_images_field():
    raw = (FIXTURES / "test_image_output.json").read_text()
    output = parse_text_output(raw)

    assert len(output.images) == 1
    assert len(output.reasoning_images) == 1
    assert output.images[0].mime == "image/jpeg"
    assert output.reasoning_images[0].mime == "image/jpeg"
    assert output.images[0].data != output.reasoning_images[0].data
    assert output.thinking.startswith("**Envisioning a Kitty Scene**")


def test_decode_wire_struct_with_three_element_array():
    from aistudio_api.infrastructure.gateway.wire_parser import (
        _decode_wire_struct,
    )

    raw = [["city", "San Francisco", "extra_field"]]
    decoded = _decode_wire_struct(raw)
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
                                                            [
                                                                "id",
                                                                [None, None, "storage"],
                                                            ],
                                                            [
                                                                "multi",
                                                                [None, None, None, 0],
                                                            ],
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


def test_parse_text_output_preserves_interleaved_parts_and_structured_slots():
    import json

    from aistudio_api.api.responses import to_gemini_parts

    part_think1 = [None, "step 1", None, None, None, None, None, None, None, None, True]
    part_exec = [None, None, None, None, None, None, None, None, [1, "print(6*7)"]]
    part_res = [None, None, None, None, None, None, None, None, None, [1, "42\n"]]
    part_think2 = [None, "step 2", None, None, None, None, None, None, None, None, True]
    part_file = [None, None, None, None, None, ["files/abc123", "video/mp4"]]
    part_vm: list[object] = [None] * 41
    part_vm[40] = {"startOffset": "0s", "endOffset": "5s", "fps": 2.0}
    part_text = [None, "42"]
    raw_cand = [
        [
            [
                part_think1,
                part_exec,
                part_res,
                part_think2,
                part_file,
                part_vm,
                part_text,
            ],
            "model",
        ],
        1,
    ]
    payload = ")]}'\n" + json.dumps([[[[raw_cand]]]])
    out = parse_text_output(payload)
    dumped = [
        p.model_dump(mode="json", exclude_none=True)
        for p in to_gemini_parts(out.candidates[0].parts)
    ]
    assert [next(iter(item.keys())) for item in dumped] == [
        "text",
        "executableCode",
        "codeExecutionResult",
        "text",
        "fileData",
        "videoMetadata",
        "text",
    ]
    assert dumped[0] == {"text": "step 1", "thought": True}
    assert dumped[1] == {"executableCode": {"language": "PYTHON", "code": "print(6*7)"}}
    assert dumped[2] == {
        "codeExecutionResult": {"outcome": "OUTCOME_OK", "output": "42\n"}
    }
    assert dumped[3] == {"text": "step 2", "thought": True}
    assert dumped[4] == {
        "fileData": {"mimeType": "video/mp4", "fileUri": "files/abc123"}
    }
    assert dumped[5] == {
        "videoMetadata": {"startOffset": "0s", "endOffset": "5s", "fps": 2.0}
    }
    assert dumped[6] == {"text": "42"}


def test_short_base64_canonical_normalization_and_validation():
    import pytest

    from aistudio_api.infrastructure.gateway.wire_types import (
        AistudioContent,
        AistudioPart,
        encode_wire_base64_str,
    )
    from aistudio_api.infrastructure.waa.signer import extract_content_digest

    # <16 chars unpadded standard & URL-safe Base64 must normalize with '=' padding
    assert encode_wire_base64_str("iVBORw0KGgo") == "iVBORw0KGgo="
    assert encode_wire_base64_str("AQIDBA") == "AQIDBA=="
    assert encode_wire_base64_str("-_8") == "+/8="
    part = AistudioPart(inline_data=("image/png", "iVBORw0KGgo"))
    assert part.to_wire()[2] == ["image/png", "iVBORw0KGgo="]
    prompt, _ = extract_content_digest([AistudioContent(role="user", parts=[part])])
    assert prompt == "iVBORw0KGgo="

    with pytest.raises(ValueError, match="Invalid Base64"):
        encode_wire_base64_str("not!valid!b64")
    with pytest.raises(ValueError, match="trailing bits"):
        encode_wire_base64_str("UklGRvaTAgBXRUJQVlA4IOqTAgDwgAmdASogBnIDPm0ylEckIy")


def test_upstream_policy_block_and_terminal_finish_reason():
    from aistudio_api.infrastructure.gateway.wire_parser import (
        is_terminal_wire_finish_reason,
        is_upstream_policy_block_text,
    )

    assert is_upstream_policy_block_text(
        "The prompt could not be submitted. This prompt may violate Google's Prohibited Use Policy."
    )
    assert not is_upstream_policy_block_text("Here is a normal response.")
    assert is_terminal_wire_finish_reason(1) is True
    assert is_terminal_wire_finish_reason("STOP") is True
    assert is_terminal_wire_finish_reason(None) is False
    assert is_terminal_wire_finish_reason(0) is False
    assert is_terminal_wire_finish_reason(22) is False
