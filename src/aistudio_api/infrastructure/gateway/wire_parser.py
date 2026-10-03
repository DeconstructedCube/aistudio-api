"""Google Protobuf wire array parser for AI Studio gateway responses.

Reverse-engineered Protobuf-over-JSON array specifications are documented in:
docs/WIRE_SPECIFICATION.md
"""

from __future__ import annotations

import json

from aistudio_api.domain.models import Candidate, GeneratedImage, ModelOutput
from aistudio_api.infrastructure.utils.common import (
    decode_base64_images,
    extract_outer_json,
)

# ==============================================================================
# Google Protobuf-over-JSON Wire Array Specifications (docs/WIRE_SPECIFICATION.md)
# ==============================================================================
# Top-level chunk indexes (WIRE_SPECIFICATION.md §7.1)
WIRE_CHUNK_CANDIDATES_INDEX = 0  # Field 1 candidates container [[candidate, ...]]
WIRE_CHUNK_USAGE_INDEX = 2  # Field 3 usage metadata array
WIRE_RESPONSE_ID_INDEX = 7  # Field 8 response unique ID string

# Candidate Part indexes inside content[0] ([parts, role]) (WIRE_SPECIFICATION.md §3.2)
WIRE_PART_THOUGHT_LEAD_INDEX = 0  # Field 1 boolean thought flag (fallback)
WIRE_PART_TEXT_INDEX = 1  # Field 2 text string
WIRE_PART_INLINE_DATA_INDEX = 2  # Field 3 [mimeType, base64_data]
WIRE_PART_FUNCTION_CALL_INDEX = 3  # Field 4 [name, args_struct, call_id]
WIRE_PART_FUNCTION_RESPONSE_INDEX = 4  # Field 5 [name, response_struct, call_id]
WIRE_PART_FILE_DATA_INDEX = 5  # Field 6 [file_id]
WIRE_PART_EXECUTABLE_CODE_INDEX = 8  # Field 9 code execution script string
WIRE_PART_CODE_EXECUTION_RESULT_INDEX = 9  # Field 10 code execution output string
WIRE_PART_THOUGHT_FLAG_INDEX = 10  # Field 11 boolean thought flag (primary)
WIRE_PART_FUNCTION_CALL_ALT_INDEX = 10  # Field 11 function call alternative slot
WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX = (
    11  # Field 12 function response alternative slot
)
WIRE_PART_THOUGHT_ALT_FLAG_INDEX = (
    12  # Field 13 integer 1 thought flag (AI Studio variant)
)
WIRE_PART_THOUGHT_SIGNATURE_INDEX = 14  # Field 15 thought cryptographic signature token

# UsageMetadata indexes inside chunk[2] (WIRE_SPECIFICATION.md §7.3)
WIRE_USAGE_PROMPT_TOKENS = 0  # Field 1 prompt token count
WIRE_USAGE_VISIBLE_COMPLETION = 1  # Field 2 visible completion token count
WIRE_USAGE_TOTAL_TOKENS = 2  # Field 3 total token count
WIRE_USAGE_CACHED_TOKENS = 3  # Field 4 cached prompt token count
WIRE_USAGE_PROMPT_DETAILS = 4  # Field 5 prompt tokens breakdown structure
WIRE_USAGE_REASONING_TOKENS = 9  # Field 10 thinking/reasoning token count

# Candidate indexes inside chunk[0][0] (WIRE_SPECIFICATION.md §7.2)
WIRE_CANDIDATE_CONTENT = 0  # Field 1 [parts, role]
WIRE_CANDIDATE_FINISH_REASON = 1  # Field 2 finish reason enum code
WIRE_CANDIDATE_FINISH_MESSAGE = 3  # Field 4 finish message string
WIRE_CANDIDATE_SAFETY_RATINGS = 4  # Field 5 safety ratings array


def _looks_like_response_chunk(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0 and isinstance(value[0], list)


def _iter_response_chunks(outer: object) -> list[list[object]]:
    if isinstance(outer, list) and outer:
        if len(outer) == 1 and isinstance(outer[0], list):
            inner = outer[0]
            nested_chunks = [
                item
                for item in inner
                if _looks_like_response_chunk(item) and isinstance(item, list)
            ]
            if nested_chunks:
                return nested_chunks
        top_level_chunks = [
            item
            for item in outer
            if _looks_like_response_chunk(item) and isinstance(item, list)
        ]
        if len(top_level_chunks) > 1:
            return top_level_chunks

    if _looks_like_response_chunk(outer) and isinstance(outer, list):
        return [outer]

    return []


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and (
            stripped.isdigit() or (stripped[0] in "+-" and stripped[1:].isdigit())
        ):
            return int(stripped)
    return None


class ResponsePart:
    """Internal intermediate representation of a decoded wire part."""

    def __init__(
        self,
        text: str = "",
        inline_data: tuple[str, str] | None = None,
        thought: bool = False,
        function_call: dict[str, object] | None = None,
        function_response: dict[str, object] | None = None,
        thought_signature: str = "",
        executable_code: object | None = None,
        code_execution_result: object | None = None,
    ):
        self.text = text
        self.inline_data = inline_data
        self.thought = thought
        self.function_call = function_call
        self.function_response = function_response
        self.thought_signature = thought_signature
        self.executable_code = executable_code
        self.code_execution_result = code_execution_result


def parse_response_part(raw_part: object) -> ResponsePart:
    if not isinstance(raw_part, list):
        return ResponsePart()

    # A content block [parts, role] or nested list is not a part.
    if len(raw_part) > 0 and isinstance(raw_part[0], list):
        return ResponsePart()

    # Guard against role name in 2-element structure where index 1 is role
    if len(raw_part) == 2 and raw_part[1] in ("model", "user", "system", "assistant"):
        return ResponsePart()

    thought = False
    if len(raw_part) > WIRE_PART_THOUGHT_FLAG_INDEX and isinstance(
        raw_part[WIRE_PART_THOUGHT_FLAG_INDEX], bool
    ):
        thought = raw_part[WIRE_PART_THOUGHT_FLAG_INDEX]
    elif len(raw_part) > WIRE_PART_THOUGHT_LEAD_INDEX and isinstance(
        raw_part[WIRE_PART_THOUGHT_LEAD_INDEX], bool
    ):
        thought = raw_part[WIRE_PART_THOUGHT_LEAD_INDEX]
    elif (
        len(raw_part) > WIRE_PART_THOUGHT_ALT_FLAG_INDEX
        and raw_part[WIRE_PART_THOUGHT_ALT_FLAG_INDEX] == 1
    ):
        thought = True

    inline_data = None
    if (
        len(raw_part) > WIRE_PART_INLINE_DATA_INDEX
        and isinstance(raw_part[WIRE_PART_INLINE_DATA_INDEX], list)
        and len(raw_part[WIRE_PART_INLINE_DATA_INDEX]) >= 2
    ):
        raw_inline = raw_part[WIRE_PART_INLINE_DATA_INDEX]
        inline_data = (str(raw_inline[0]), str(raw_inline[1]))

    function_call = _coerce_wire_payload(
        raw_part[WIRE_PART_FUNCTION_CALL_INDEX]
        if len(raw_part) > WIRE_PART_FUNCTION_CALL_INDEX
        else None,
        "functionCall",
    )
    if function_call is None and len(raw_part) > WIRE_PART_FUNCTION_CALL_ALT_INDEX:
        function_call = _coerce_wire_payload(
            raw_part[WIRE_PART_FUNCTION_CALL_ALT_INDEX], "functionCall"
        )
    thought_signature = (
        raw_part[WIRE_PART_THOUGHT_SIGNATURE_INDEX]
        if len(raw_part) > WIRE_PART_THOUGHT_SIGNATURE_INDEX
        and isinstance(raw_part[WIRE_PART_THOUGHT_SIGNATURE_INDEX], str)
        else ""
    )
    if function_call is not None:
        if thought_signature:
            function_call["thought_signature"] = thought_signature
        raw = function_call.get("raw")
        if isinstance(raw, list) and len(raw) > 2 and isinstance(raw[2], str):
            function_call["call_id"] = raw[2]

    function_response = _coerce_wire_payload(
        raw_part[WIRE_PART_FUNCTION_RESPONSE_INDEX]
        if len(raw_part) > WIRE_PART_FUNCTION_RESPONSE_INDEX
        else None,
        "functionResponse",
    )
    if (
        function_response is None
        and len(raw_part) > WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX
    ):
        function_response = _coerce_wire_payload(
            raw_part[WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX], "functionResponse"
        )

    executable_code = (
        raw_part[WIRE_PART_EXECUTABLE_CODE_INDEX]
        if len(raw_part) > WIRE_PART_EXECUTABLE_CODE_INDEX
        else None
    )
    code_execution_result = (
        raw_part[WIRE_PART_CODE_EXECUTION_RESULT_INDEX]
        if len(raw_part) > WIRE_PART_CODE_EXECUTION_RESULT_INDEX
        else None
    )

    return ResponsePart(
        text=raw_part[WIRE_PART_TEXT_INDEX]
        if len(raw_part) > WIRE_PART_TEXT_INDEX
        and isinstance(raw_part[WIRE_PART_TEXT_INDEX], str)
        else "",
        inline_data=inline_data,
        thought=thought,
        function_call=function_call,
        function_response=function_response,
        thought_signature=thought_signature,
        executable_code=executable_code,
        code_execution_result=code_execution_result,
    )


def _coerce_wire_payload(
    raw_value: object, payload_type: str
) -> dict[str, object] | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        dict_payload = dict(raw_value)
        dict_payload.setdefault("type", payload_type)
        return dict_payload
    if isinstance(raw_value, list):
        list_payload: dict[str, object] = {"type": payload_type, "raw": raw_value}
        if raw_value and isinstance(raw_value[0], str):
            list_payload["name"] = raw_value[0]
        if len(raw_value) > 1:
            second = raw_value[1]
            if isinstance(second, dict):
                list_payload["args"] = second
            elif isinstance(second, list):
                list_payload["args"] = _decode_wire_struct(second)
            elif isinstance(second, str):
                stripped = second.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        list_payload["args"] = json.loads(second)
                    except json.JSONDecodeError:
                        list_payload["arguments"] = second
                else:
                    list_payload["arguments"] = second
            elif second is not None:
                list_payload["args"] = second
        if len(raw_value) > 2 and isinstance(raw_value[2], str):
            list_payload["call_id"] = raw_value[2]
        return list_payload
    return {"type": payload_type, "raw": raw_value}


def _is_wire_value(val: object) -> bool:
    if isinstance(val, list) and len(val) > 0:
        return val[0] is None or (val[0] == 0 and len(val) == 1)
    return False


def _decode_wire_struct(raw_struct: object) -> dict[str, object]:
    if isinstance(raw_struct, dict):
        return raw_struct
    if not isinstance(raw_struct, list) or not raw_struct:
        return {}

    # Single unboxed key-value pair [key, wire_value]
    if len(raw_struct) >= 2 and isinstance(raw_struct[0], str):
        return {raw_struct[0]: _decode_wire_value(raw_struct[1])}
    entries: object = raw_struct
    while (
        isinstance(entries, list)
        and len(entries) == 1
        and isinstance(entries[0], list)
        and not (len(entries[0]) >= 2 and isinstance(entries[0][0], str))
    ):
        entries = entries[0]

    if not isinstance(entries, list):
        return {}

    result: dict[str, object] = {}
    for item in entries:
        if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str):
            result[item[0]] = _decode_wire_value(item[1])
    return result


def _decode_wire_list(raw_list: object) -> list[object]:
    if not isinstance(raw_list, list) or not raw_list:
        return []

    if (
        len(raw_list) == 1
        and isinstance(raw_list[0], list)
        and not _is_wire_value(raw_list[0])
    ):
        items = raw_list[0]
    else:
        items = raw_list

    return [_decode_wire_value(item) for item in items]


def _decode_wire_value(value: object) -> object:
    if not isinstance(value, list):
        return value
    if len(value) == 0:
        return []
    if len(value) == 1 and value[0] == 0:
        return None
    if (
        len(value) >= 2
        and isinstance(value[1], (int, float))
        and not isinstance(value[1], bool)
    ):
        return value[1]
    if len(value) >= 3 and isinstance(value[2], str):
        return value[2]
    if len(value) >= 4 and value[3] is not None:
        if isinstance(value[3], bool):
            return value[3]
        if value[3] in (0, 1):
            return bool(value[3])
    if len(value) >= 5 and value[4] is not None:
        return _decode_wire_struct(value[4])
    if len(value) >= 6 and value[5] is not None:
        return _decode_wire_list(value[5])
    if len(value) >= 3 and value[2] is not None:
        return value[2]
    return [_decode_wire_value(item) for item in value]

def parse_usage_metadata(raw_usage: object) -> dict[str, object]:
    if not isinstance(raw_usage, list):
        return {}
    prompt_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_PROMPT_TOKENS]
        if len(raw_usage) > WIRE_USAGE_PROMPT_TOKENS
        else None
    )
    visible_completion_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_VISIBLE_COMPLETION]
        if len(raw_usage) > WIRE_USAGE_VISIBLE_COMPLETION
        else None
    )
    total_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_TOTAL_TOKENS]
        if len(raw_usage) > WIRE_USAGE_TOTAL_TOKENS
        else None
    )
    cached_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_CACHED_TOKENS]
        if len(raw_usage) > WIRE_USAGE_CACHED_TOKENS
        else None
    )
    reasoning_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_REASONING_TOKENS]
        if len(raw_usage) > WIRE_USAGE_REASONING_TOKENS
        else None
    )
    completion_tokens = visible_completion_tokens
    if isinstance(visible_completion_tokens, int) and isinstance(reasoning_tokens, int):
        completion_tokens = visible_completion_tokens + reasoning_tokens
    elif completion_tokens is None:
        completion_tokens = reasoning_tokens
    if (
        total_tokens is None
        and isinstance(prompt_tokens, int)
        and isinstance(completion_tokens, int)
    ):
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "prompt_tokens_details": (
            raw_usage[WIRE_USAGE_PROMPT_DETAILS]
            if len(raw_usage) > WIRE_USAGE_PROMPT_DETAILS
            else None
        ),
        "completion_tokens_details": {
            "reasoning_tokens": reasoning_tokens or 0,
            "visible_tokens": visible_completion_tokens or 0,
        },
    }


def parse_chunk_usage(chunk: object) -> dict[str, object]:
    if not isinstance(chunk, list):
        return {}
    return parse_usage_metadata(
        chunk[WIRE_CHUNK_USAGE_INDEX] if len(chunk) > WIRE_CHUNK_USAGE_INDEX else None
    )


def parse_response_chunk(chunk: list[object]) -> Candidate:
    candidate = Candidate()
    if (
        not isinstance(chunk, list)
        or not chunk
        or len(chunk) <= WIRE_CHUNK_CANDIDATES_INDEX
    ):
        return candidate

    candidates_container = chunk[WIRE_CHUNK_CANDIDATES_INDEX]
    if not isinstance(candidates_container, list) or not candidates_container:
        return candidate

    raw_candidate = candidates_container[0]
    if not isinstance(raw_candidate, list):
        return candidate

    raw_content = (
        raw_candidate[WIRE_CANDIDATE_CONTENT]
        if len(raw_candidate) > WIRE_CANDIDATE_CONTENT
        else None
    )
    raw_parts = (
        raw_content[0] if isinstance(raw_content, list) and len(raw_content) > 0 else []
    )

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    images: list[GeneratedImage] = []
    reasoning_images: list[GeneratedImage] = []
    function_calls: list[dict[str, object]] = []
    function_responses: list[dict[str, object]] = []
    thought_signature = ""
    code_outputs: list[str] = []

    for raw_part in raw_parts if isinstance(raw_parts, list) else []:
        part = parse_response_part(raw_part)
        if part.inline_data:
            decoded = decode_base64_images(
                [{"mime": part.inline_data[0], "data": part.inline_data[1]}]
            )
            decoded_images: list[GeneratedImage] = []
            for img in decoded:
                raw_bytes = img.get("bytes")
                b_data = (
                    bytes(raw_bytes)
                    if isinstance(raw_bytes, (bytes, bytearray))
                    else b""
                )
                decoded_images.append(
                    GeneratedImage(
                        mime=str(img.get("mime", "image/jpeg")),
                        data=b_data,
                        size=int(str(img.get("size", 0))),
                        thought_signature=part.thought_signature or "",
                    )
                )
            if part.thought:
                reasoning_images.extend(decoded_images)
            else:
                images.extend(decoded_images)
        if part.executable_code:
            code_outputs.append(str(part.executable_code))
        if part.code_execution_result:
            code_outputs.append(str(part.code_execution_result))
        if part.function_call:
            function_calls.append(part.function_call)
        if part.function_response:
            function_responses.append(part.function_response)
        if part.thought_signature:
            thought_signature = part.thought_signature
        if part.text:
            if part.thought:
                thinking_parts.append(part.text)
            else:
                text_parts.append(part.text)

    candidate.text = "".join(text_parts)
    candidate.thinking = "".join(thinking_parts)
    candidate.images = images
    candidate.reasoning_images = reasoning_images
    candidate.function_calls = function_calls
    candidate.function_responses = function_responses
    candidate.thought_signature = thought_signature
    candidate.code_output = "\n".join(code_outputs)
    candidate.finish_reason = (
        raw_candidate[WIRE_CANDIDATE_FINISH_REASON]
        if len(raw_candidate) > WIRE_CANDIDATE_FINISH_REASON
        and isinstance(raw_candidate[WIRE_CANDIDATE_FINISH_REASON], int)
        else None
    )
    candidate.finish_message = (
        str(raw_candidate[WIRE_CANDIDATE_FINISH_MESSAGE])
        if len(raw_candidate) > WIRE_CANDIDATE_FINISH_MESSAGE
        and isinstance(raw_candidate[WIRE_CANDIDATE_FINISH_MESSAGE], str)
        else ""
    )
    raw_ratings = (
        raw_candidate[WIRE_CANDIDATE_SAFETY_RATINGS]
        if len(raw_candidate) > WIRE_CANDIDATE_SAFETY_RATINGS
        else None
    )
    candidate.safety_ratings = raw_ratings if isinstance(raw_ratings, list) else []
    return candidate


def parse_text_output(raw: str) -> ModelOutput:
    output = ModelOutput(raw_response=raw)

    parts = extract_outer_json(raw)
    if not parts:
        return output

    outer = parts[0]
    chunks = _iter_response_chunks(outer)
    if not chunks:
        return output

    merged = Candidate()
    for chunk in chunks:
        parsed = parse_response_chunk(chunk)
        if parsed.text:
            merged.text += parsed.text
        if parsed.thinking:
            merged.thinking += parsed.thinking
        if parsed.images:
            merged.images.extend(parsed.images)
        if parsed.reasoning_images:
            merged.reasoning_images.extend(parsed.reasoning_images)
        if parsed.function_calls:
            merged.function_calls.extend(parsed.function_calls)
        if parsed.function_responses:
            merged.function_responses.extend(parsed.function_responses)
        if parsed.thought_signature:
            merged.thought_signature = parsed.thought_signature
        if parsed.code_output:
            merged.code_output = "\n".join(
                filter(None, [merged.code_output, parsed.code_output])
            )
        if parsed.finish_reason is not None:
            merged.finish_reason = parsed.finish_reason
        if parsed.finish_message:
            merged.finish_message = parsed.finish_message
        if parsed.safety_ratings:
            merged.safety_ratings = parsed.safety_ratings

    last_chunk = chunks[-1]
    output.usage = parse_usage_metadata(
        last_chunk[WIRE_CHUNK_USAGE_INDEX]
        if len(last_chunk) > WIRE_CHUNK_USAGE_INDEX
        else None
    )
    raw_rid = (
        last_chunk[WIRE_RESPONSE_ID_INDEX]
        if len(last_chunk) > WIRE_RESPONSE_ID_INDEX
        else None
    )
    output.response_id = str(raw_rid) if isinstance(raw_rid, str) else ""
    output.candidates = [merged]
    return output


def parse_image_output(raw: str) -> ModelOutput:
    return parse_text_output(raw)


__all__ = [
    "WIRE_CANDIDATE_CONTENT",
    "WIRE_CANDIDATE_FINISH_MESSAGE",
    "WIRE_CANDIDATE_FINISH_REASON",
    "WIRE_CANDIDATE_SAFETY_RATINGS",
    "WIRE_CHUNK_CANDIDATES_INDEX",
    "WIRE_CHUNK_USAGE_INDEX",
    "WIRE_PART_CODE_EXECUTION_RESULT_INDEX",
    "WIRE_PART_EXECUTABLE_CODE_INDEX",
    "WIRE_PART_FILE_DATA_INDEX",
    "WIRE_PART_FUNCTION_CALL_ALT_INDEX",
    "WIRE_PART_FUNCTION_CALL_INDEX",
    "WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX",
    "WIRE_PART_FUNCTION_RESPONSE_INDEX",
    "WIRE_PART_INLINE_DATA_INDEX",
    "WIRE_PART_TEXT_INDEX",
    "WIRE_PART_THOUGHT_ALT_FLAG_INDEX",
    "WIRE_PART_THOUGHT_FLAG_INDEX",
    "WIRE_PART_THOUGHT_LEAD_INDEX",
    "WIRE_PART_THOUGHT_SIGNATURE_INDEX",
    "WIRE_RESPONSE_ID_INDEX",
    "WIRE_USAGE_CACHED_TOKENS",
    "WIRE_USAGE_PROMPT_DETAILS",
    "WIRE_USAGE_PROMPT_TOKENS",
    "WIRE_USAGE_REASONING_TOKENS",
    "WIRE_USAGE_TOTAL_TOKENS",
    "WIRE_USAGE_VISIBLE_COMPLETION",
    "ResponsePart",
    "parse_chunk_usage",
    "parse_image_output",
    "parse_response_chunk",
    "parse_response_part",
    "parse_text_output",
    "parse_usage_metadata",
]
