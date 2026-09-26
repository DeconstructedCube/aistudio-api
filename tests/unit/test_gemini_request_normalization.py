import pytest

from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiGenerationConfig,
    GeminiPart,
)
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.infrastructure.gateway.wire_types import AistudioImageOutputMode


def test_normalize_gemini_request_exposes_generation_config_overrides():
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])],
        generationConfig=GeminiGenerationConfig(
            stopSequences=["6"],
            temperature=1,
            topP=0.95,
            topK=64,
            maxOutputTokens=65536,
            responseMimeType="text/plain",
            responseSchema={
                "type": "object",
                "properties": {"test_response": {"type": "string"}},
                "propertyOrdering": ["test_response"],
            },
            presencePenalty=0.1,
            frequencyPenalty=0.2,
            responseLogprobs=True,
            logprobs=5,
            mediaResolution=2,
            thinkingConfig=[1, None, None, 3],
        ),
    )

    normalized = normalize_gemini_request(req, "models/gemini-3.1-flash-image-preview")

    assert normalized["generation_config_overrides"] == {
        "stop_sequences": ["6"],
        "max_tokens": 65536,
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 64,
        "response_mime_type": "text/plain",
        "response_schema": [
            6,
            None,
            None,
            None,
            None,
            None,
            [["test_response", [1]]],
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
            None,
            None,
            None,
            None,
            None,
            ["test_response"],
        ],
        "presence_penalty": 0.1,
        "frequency_penalty": 0.2,
        "response_logprobs": True,
        "logprobs": 5,
        "image_output_mode": AistudioImageOutputMode.image_only(),
        "media_resolution": 2,
        "thinking_config": [1, None, None, 3],
    }


def test_normalize_gemini_request_maps_official_image_generation_fields():
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": "/9j/4AAQSkZJRgABAQAA....",
                            }
                        },
                        {"text": "INSERT_INPUT_HERE"},
                    ],
                }
            ],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
                "thinkingConfig": {"thinkingLevel": "HIGH"},
                "imageConfig": {
                    "aspectRatio": "9:16",
                    "imageSize": "4K",
                    "personGeneration": "",
                },
            },
            "tools": [
                {
                    "googleSearch": {
                        "searchTypes": {
                            "webSearch": {},
                            "imageSearch": {},
                        }
                    }
                }
            ],
        }
    )

    normalized = normalize_gemini_request(req, "models/gemini-3.1-flash-image-preview")

    assert normalized["tools"] == [[None, None, None, [None, [[], []]]]]
    assert normalized["generation_config_overrides"] == {
        "image_output_mode": AistudioImageOutputMode.text_and_image(),
        "thinking_config": [1, None, None, 3],
        "output_resolution": ["9:16", "4K"],
    }
    assert normalized.capture_images is not None
    assert len(normalized.capture_images) == 1
    assert normalized.contents[0].parts[0].inline_data == (
        "image/jpeg",
        "/9j/4AAQSkZJRgABAQAA....",
    )


def test_normalize_gemini_request_encodes_function_declarations_to_wire_tools():
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "getWeather",
                            "description": "gets the weather for a requested city",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "propertyOrdering": ["city"],
                            },
                        }
                    ]
                }
            ],
        }
    )
    normalized = normalize_gemini_request(req, "models/gemma-4-31b-it")

    assert normalized["tools"] == [
        [
            None,
            [
                [
                    "getWeather",
                    "gets the weather for a requested city",
                    [
                        6,
                        None,
                        None,
                        None,
                        None,
                        None,
                        [["city", [1]]],
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
                        None,
                        None,
                        None,
                        None,
                        None,
                        ["city"],
                    ],
                ]
            ],
        ],
    ]


def test_normalize_gemini_request_applies_gemma_default_tools():
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])],
    )

    normalized = normalize_gemini_request(req, "models/gemma-4-31b-it")

    assert normalized["tools"] == [[None, None, None, [None, [[]]]]]


def test_normalize_gemini_request_encodes_builtin_tools_to_wire():
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
            "tools": [
                {
                    "googleSearch": {},
                    "googleMaps": {},
                    "urlContext": {},
                    "codeExecution": {},
                }
            ],
        }
    )
    normalized = normalize_gemini_request(req, "models/gemini-3.5-flash")

    assert normalized["tools"] == [
        [[]],
        [None, None, None, [None, [[]]]],
        [None, None, None, None, None, None, None, None, None, None, []],
        [None, None, None, None, None, None, None, []],
    ]


def test_normalize_gemini_request_rejects_gemma_unsupported_builtin_tool():
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
            "tools": [
                {
                    "googleMaps": {},
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="not allowed"):
        normalize_gemini_request(req, "models/gemma-4-31b-it")


def test_normalize_gemini_request_empty_tools_disables_model_defaults():
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])],
        tools=[],
    )

    normalized = normalize_gemini_request(req, "models/gemma-4-31b-it")

    assert normalized["tools"] == []


def test_normalize_gemini_request_rejects_unsupported_person_generation():
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
            "generationConfig": {
                "imageConfig": {
                    "personGeneration": "ALLOW_ADULT",
                }
            },
        }
    )

    with pytest.raises(ValueError, match="personGeneration"):
        normalize_gemini_request(req, "models/gemini-3.1-flash-image-preview")


def test_normalize_gemini_request_maps_official_text_model_fields():
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "INSERT_INPUT_HERE"},
                    ],
                }
            ],
            "generationConfig": {
                "thinkingConfig": {"thinkingLevel": "HIGH"},
            },
            "safetySettings": [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_LOW_AND_ABOVE",
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_ONLY_HIGH",
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE",
                },
            ],
            "tools": [
                {"urlContext": {}},
                {"codeExecution": {}},
                {"googleSearch": {}},
            ],
        }
    )

    normalized = normalize_gemini_request(req, "models/gemini-3.5-flash")

    assert normalized["tools"] == [
        [None, None, None, None, None, None, None, []],
        [[]],
        [None, None, None, [None, [[]]]],
    ]
    assert normalized["generation_config_overrides"] == {
        "thinking_config": [1, None, None, 3],
    }
    assert normalized["safety_settings"] == [
        [None, None, 7, 1],
        [None, None, 8, 4],
        [None, None, 9, 3],
        [None, None, 10, 2],
    ]


def test_normalize_gemini_request_drops_unknown_safety_category_when_enabled(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  drop_unsupported_params: true
  profiles:
    - name: gemini_models
      match:
        prefixes: [gemini-]
      drop_unsupported_params: true
"""
    )
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(config_path))
    from aistudio_api.infrastructure.gateway.model_defaults import (
        invalidate_config_cache,
    )

    invalidate_config_cache()

    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
            "safetySettings": [
                {
                    "category": "HARM_CATEGORY_CIVIC_INTEGRITY",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE",
                },
            ],
        }
    )

    normalized = normalize_gemini_request(req, "models/gemini-2.5-flash")
    assert normalized["safety_settings"] == [[None, None, 7, 4]]
    invalidate_config_cache()


def test_normalize_gemini_request_rejects_unknown_safety_category_when_disabled(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_defaults:
  drop_unsupported_params: false
  profiles:
    - name: gemini_models
      match:
        prefixes: [gemini-]
      drop_unsupported_params: false
"""
    )
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(config_path))
    from aistudio_api.infrastructure.gateway.model_defaults import (
        invalidate_config_cache,
    )

    invalidate_config_cache()

    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
            "safetySettings": [
                {
                    "category": "HARM_CATEGORY_CIVIC_INTEGRITY",
                    "threshold": "BLOCK_NONE",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="Unsupported safety category"):
        normalize_gemini_request(req, "models/gemini-2.5-flash")
    invalidate_config_cache()


def test_normalize_gemini_request_mode_none_suppresses_tools():
    """Verify toolConfig mode=NONE suppresses tools for pure text output."""
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {"role": "user", "parts": [{"text": "Hello, please summarize."}]}
            ],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "read",
                            "description": "read file",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                            },
                        }
                    ]
                }
            ],
            "toolConfig": {
                "functionCallingConfig": {
                    "mode": "NONE",
                }
            },
        }
    )
    normalized = normalize_gemini_request(req, "models/gemini-3.8-flash")
    # Tools and tool_config must both be None so MakerSuite generates text without calling functions
    assert normalized.tools is None
    assert normalized.tool_config is None


def test_normalize_gemini_request_supports_snake_case_function_declarations():
    """Verify tools with snake_case function_declarations (e.g. SillyTavern anti-truncation) are correctly converted."""
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": "emit_complete_response_f8dce5fa2102a5307ad6412d",
                            "description": "Emit the complete final user-visible reply exactly once.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "string",
                                        "description": "The complete final reply shown to the user.",
                                    }
                                },
                                "required": ["content"],
                                "property_ordering": ["content"],
                            },
                        }
                    ]
                }
            ],
            "tool_config": {
                "function_calling_config": {
                    "mode": "AUTO",
                }
            },
        }
    )
    normalized = normalize_gemini_request(req, "models/gemini-3.8-flash")
    assert normalized.tools is not None
    assert len(normalized.tools) == 1
    # Single Tool entry with function declarations at index 1
    assert normalized.tools[0][0] is None
    func_decls = normalized.tools[0][1]
    assert isinstance(func_decls, list)
    assert len(func_decls) == 1
    decl_entry = func_decls[0]
    assert isinstance(decl_entry, list)
    assert decl_entry[0] == "emit_complete_response_f8dce5fa2102a5307ad6412d"
    assert decl_entry[1] == "Emit the complete final user-visible reply exactly once."
    # Parameters schema wire format
    schema_wire = decl_entry[2]
    assert isinstance(schema_wire, list)
    assert schema_wire[0] == 6  # OBJECT
    assert schema_wire[6] == [
        ["content", [1, None, "The complete final reply shown to the user."]]
    ]
    assert schema_wire[7] == ["content"]
    assert schema_wire[22] == ["content"]


def test_encode_schema_to_wire_lossless_metadata():
    """Verify encode_schema_to_wire preserves description, enum, format, nullable, anyOf, default, constraints."""
    from aistudio_api.application.chat_service import encode_schema_to_wire

    schema = {
        "type": "object",
        "title": "TaskPayload",
        "description": "Schema for task operations",
        "format": "custom-format",
        "nullable": False,
        "minProperties": 1,
        "maxProperties": 10,
        "properties": {
            "op": {
                "type": "string",
                "description": "Operation type",
                "enum": ["init", "start", "done", "rm"],
            },
            "count": {
                "type": "integer",
                "description": "Number of items",
                "minimum": 1,
                "maximum": 100,
                "default": 1,
            },
            "items": {
                "type": "array",
                "description": "List of task strings",
                "minItems": 1,
                "maxItems": 50,
                "items": {"type": "string", "maxLength": 128},
            },
            "options": {
                "anyOf": [
                    {"type": "string", "description": "String option"},
                    {"type": "boolean", "description": "Bool option"},
                ]
            },
        },
        "required": ["op"],
        "propertyOrdering": ["op", "count", "items", "options"],
    }

    wire = encode_schema_to_wire(schema)

    # Wire array checks
    assert wire[0] == 6  # OBJECT
    assert wire[1] == "custom-format"  # Field 2: format
    assert wire[2] == "Schema for task operations"  # Field 3: description
    assert wire[3] is False  # Field 4: nullable
    assert wire[7] == ["op"]  # Field 8: required
    assert wire[8] == 1  # Field 9: minProperties
    assert wire[9] == 10  # Field 10: maxProperties
    assert wire[22] == ["op", "count", "items", "options"]  # Field 23: propertyOrdering
    assert wire[23] == "TaskPayload"  # Field 24: title

    # Properties checks
    assert isinstance(wire[6], list)
    props: dict[str, object] = {
        str(item[0]): item[1]
        for item in wire[6]
        if isinstance(item, list) and len(item) >= 2
    }
    # op property
    op_wire = props["op"]
    assert isinstance(op_wire, list)
    assert op_wire[0] == 1  # STRING
    assert op_wire[2] == "Operation type"  # Field 3: description
    assert op_wire[4] == ["init", "start", "done", "rm"]  # Field 5: enum

    # count property
    count_wire = props["count"]
    assert isinstance(count_wire, list)
    assert count_wire[0] == 3  # INTEGER
    assert count_wire[2] == "Number of items"
    assert count_wire[10] == 1  # minimum
    assert count_wire[11] == 100  # maximum
    assert count_wire[24] == [None, 1]  # default Value node for integer 1

    # items property
    items_wire = props["items"]
    assert isinstance(items_wire, list)
    assert items_wire[0] == 5  # ARRAY
    assert items_wire[2] == "List of task strings"
    items_sub_wire = items_wire[5]
    assert isinstance(items_sub_wire, list)
    assert items_sub_wire[0] == 1  # items schema string
    assert items_sub_wire[13] == 128  # items schema maxLength
    assert items_wire[20] == 50  # maxItems
    assert items_wire[21] == 1  # minItems

    # options property (anyOf)
    options_wire = props["options"]
    assert isinstance(options_wire, list)
    any_of_list = options_wire[17]
    assert isinstance(any_of_list, list)
    assert len(any_of_list) == 2
    first_sub = any_of_list[0]
    assert isinstance(first_sub, list)
    assert first_sub[0] == 1  # string
    assert first_sub[2] == "String option"
    second_sub = any_of_list[1]
    assert isinstance(second_sub, list)
    assert second_sub[0] == 4  # boolean
    assert second_sub[2] == "Bool option"
