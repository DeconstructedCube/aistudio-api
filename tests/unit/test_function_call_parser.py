from aistudio_api.infrastructure.gateway.wire_parser import parse_response_chunk


def test_parse_response_chunk_keeps_raw_function_call_and_response():
    chunk = [
        [
            [
                [
                    [
                        [None, None, None, ["getWeather", '{"city":"Shanghai"}']],
                        [
                            None,
                            None,
                            None,
                            None,
                            ["getWeather", {"city": "Shanghai", "temperature": "24C"}],
                        ],
                    ]
                ],
                1,
            ]
        ],
        None,
        [5, 1, 6],
        None,
        None,
        None,
        None,
        "resp_123",
    ]

    candidate = parse_response_chunk(chunk)

    assert candidate.function_calls == [
        {
            "type": "functionCall",
            "raw": ["getWeather", '{"city":"Shanghai"}'],
            "name": "getWeather",
            "args": {"city": "Shanghai"},
        }
    ]
    assert candidate.function_responses == [
        {
            "type": "functionResponse",
            "raw": ["getWeather", {"city": "Shanghai", "temperature": "24C"}],
            "name": "getWeather",
            "args": {"city": "Shanghai", "temperature": "24C"},
        }
    ]


def test_parse_response_chunk_extracts_real_aistudio_function_call_shape():
    chunk = [
        [
            [
                [
                    [
                        [
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
                            [
                                "getWeather",
                                [[["city", [None, None, "Shanghai"]]]],
                                "e6ni61kr",
                            ],
                            None,
                            None,
                            None,
                            "EiYKJGUyNDgzMGE3LTVjZDYtNDJmZS05OThiLWVlNTM5ZTcyYjljMw==",
                        ]
                    ],
                    "model",
                ]
            ]
        ],
        None,
        [52, 15, 147, None, [[1, 52]], None, None, None, None, 80],
        None,
        None,
        None,
        None,
        "resp_real",
    ]

    candidate = parse_response_chunk(chunk)

    assert candidate.function_calls == [
        {
            "type": "functionCall",
            "raw": ["getWeather", [[["city", [None, None, "Shanghai"]]]], "e6ni61kr"],
            "name": "getWeather",
            "args": {"city": "Shanghai"},
            "call_id": "e6ni61kr",
            "thought_signature": "EiYKJGUyNDgzMGE3LTVjZDYtNDJmZS05OThiLWVlNTM5ZTcyYjljMw==",
        }
    ]

def test_to_gemini_parts_preserves_thought_signature_and_id():
    from aistudio_api.api.responses import to_gemini_parts

    fcs = [
        {
            "name": "getWeather",
            "args": {"city": "Tokyo"},
            "call_id": "call_abc123",
            "thought_signature": "sig_xyz789",
        }
    ]
    frs = [
        {
            "name": "getWeather",
            "args": {"temperature": "20C"},
            "call_id": "call_abc123",
        }
    ]

    parts = to_gemini_parts("", function_calls=fcs, function_responses=frs)
    assert len(parts) == 2

    # FunctionCall part
    fc_part = parts[0]
    assert fc_part.functionCall is not None
    assert fc_part.functionCall.name == "getWeather"
    assert fc_part.functionCall.args == {"city": "Tokyo"}
    assert fc_part.functionCall.id == "call_abc123"
    assert fc_part.thoughtSignature == "sig_xyz789"

    # FunctionResponse part
    fr_part = parts[1]
    assert fr_part.functionResponse is not None
    assert fr_part.functionResponse.name == "getWeather"
    assert fr_part.functionResponse.response == {"temperature": "20C"}
    assert fr_part.functionResponse.id == "call_abc123"


def test_encode_schema_to_wire_case_insensitive_and_aliases():
    from aistudio_api.application.chat_service import encode_schema_to_wire

    schema = {
        "type": "OBJECT",
        "properties": {
            "location": {"type": "STRING"},
            "count": {"type": "INTEGER"},
            "ratio": {"type": "Type.FLOAT"},
            "active": {"type": "BOOLEAN"},
            "tags": {"type": "ARRAY", "items": {"type": "string"}},
        },
        "required": ["location"],
    }

    wire = encode_schema_to_wire(schema)
    assert wire[0] == 6  # OBJECT
    props = dict(wire[6])
    assert props["location"] == [1]  # STRING
    assert props["count"] == [3]  # INTEGER
    assert props["ratio"] == [2]  # FLOAT
    assert props["active"] == [4]  # BOOLEAN
    assert props["tags"][0] == 5  # ARRAY
    assert props["tags"][5] == [1]  # ARRAY items STRING
    assert wire[7] == ["location"]  # required


def test_wire_args_nested_struct_encoding():
    from aistudio_api.infrastructure.gateway.wire_types import _encode_wire_args

    nested_data = {
        "name": "get_current_weather",
        "content": {"temperature": "15C", "condition": "Sunny", "metrics": {"uv": 3}},
    }

    encoded = _encode_wire_args(nested_data)
    # Must be [ [ [key, val], ... ] ]
    assert isinstance(encoded, list) and len(encoded) == 1
    fields = dict(encoded[0])
    assert fields["name"] == [None, None, "get_current_weather"]
    # content should be [None, [ [key, val], ... ] ]
    content_val = fields["content"]
    assert content_val[0] is None
    content_fields = dict(content_val[1])
    assert content_fields["temperature"] == [None, None, "15C"]
    assert content_fields["condition"] == [None, None, "Sunny"]
    metrics_val = content_fields["metrics"]
    assert metrics_val[0] is None
    metrics_fields = dict(metrics_val[1])
    assert metrics_fields["uv"] == [1, 3]


def test_chat_service_does_not_convert_signed_text_into_thought():
    from aistudio_api.api.schemas import (
        GeminiContent,
        GeminiGenerateContentRequest,
        GeminiPart,
    )
    from aistudio_api.application.chat_service import normalize_gemini_request

    req = GeminiGenerateContentRequest(
        contents=[
            GeminiContent(
                role="model",
                parts=[
                    GeminiPart(text="Normal answer", thoughtSignature="valid_sig_123"),
                ],
            )
        ]
    )
    normalized = normalize_gemini_request(req, "gemini-3.7-flash")
    part = normalized.contents[0].parts[0]
    assert part.text == "Normal answer"
    assert part.thought is False
    assert part.thought_signature == "valid_sig_123"

    # Wire serialization must not have thought flag at index 12
    wire = part.to_wire()
    assert wire[1] == "Normal answer"
    assert len(wire) > 14
    assert wire[14] == "valid_sig_123"
    assert wire[12] is None
