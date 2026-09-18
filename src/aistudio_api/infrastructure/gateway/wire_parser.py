"""Google Protobuf wire array parser for AI Studio gateway responses."""

from __future__ import annotations

import json

from aistudio_api.domain.models import Candidate, GeneratedImage, ModelOutput
from aistudio_api.infrastructure.utils.common import (
    decode_base64_images,
    extract_outer_json,
)


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

    thought = False
    if len(raw_part) > 10 and isinstance(raw_part[10], bool):
        thought = raw_part[10]
    elif len(raw_part) > 0 and isinstance(raw_part[0], bool):
        thought = raw_part[0]
    elif len(raw_part) > 12 and raw_part[12] == 1:
        thought = True

    inline_data = None
    if len(raw_part) > 2 and isinstance(raw_part[2], list) and len(raw_part[2]) >= 2:
        inline_data = (str(raw_part[2][0]), str(raw_part[2][1]))

    function_call = _coerce_wire_payload(
        raw_part[3] if len(raw_part) > 3 else None, "functionCall"
    )
    if function_call is None and len(raw_part) > 10:
        function_call = _coerce_wire_payload(raw_part[10], "functionCall")
    thought_signature = (
        raw_part[14] if len(raw_part) > 14 and isinstance(raw_part[14], str) else ""
    )
    if function_call is not None:
        if thought_signature:
            function_call["thought_signature"] = thought_signature
        raw = function_call.get("raw")
        if isinstance(raw, list) and len(raw) > 2 and isinstance(raw[2], str):
            function_call["call_id"] = raw[2]
    function_response = _coerce_wire_payload(
        raw_part[4] if len(raw_part) > 4 else None, "functionResponse"
    )
    executable_code = raw_part[8] if len(raw_part) > 8 else None
    code_execution_result = raw_part[9] if len(raw_part) > 9 else None

    return ResponsePart(
        text=raw_part[1] if len(raw_part) > 1 and isinstance(raw_part[1], str) else "",
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
                list_payload["args"] = _decode_wire_argument_pairs(second)
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


def _decode_wire_argument_pairs(raw_args: object) -> object:
    if not isinstance(raw_args, list):
        return raw_args

    if all(
        isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str)
        for item in raw_args
    ):
        result = {}
        for key, value in raw_args:
            result[key] = _decode_wire_value(value)
        return result

    if len(raw_args) == 1 and isinstance(raw_args[0], list):
        return _decode_wire_argument_pairs(raw_args[0])

    return [_decode_wire_value(item) for item in raw_args]


def _decode_wire_value(value: object) -> object:
    if isinstance(value, list):
        if len(value) >= 3 and value[2] is not None:
            return value[2]
        decoded = _decode_wire_argument_pairs(value)
        if decoded != value:
            return decoded
    return value


def parse_usage_metadata(raw_usage: object) -> dict[str, object]:
    if not isinstance(raw_usage, list):
        return {}
    prompt_tokens = _coerce_int(raw_usage[0] if len(raw_usage) > 0 else None)
    visible_completion_tokens = _coerce_int(
        raw_usage[1] if len(raw_usage) > 1 else None
    )
    total_tokens = _coerce_int(raw_usage[2] if len(raw_usage) > 2 else None)
    cached_tokens = _coerce_int(raw_usage[3] if len(raw_usage) > 3 else None)
    reasoning_tokens = _coerce_int(raw_usage[9] if len(raw_usage) > 9 else None)
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
        "prompt_tokens_details": raw_usage[4] if len(raw_usage) > 4 else None,
        "completion_tokens_details": {
            "reasoning_tokens": reasoning_tokens or 0,
            "visible_tokens": visible_completion_tokens or 0,
        },
    }


def parse_chunk_usage(chunk: object) -> dict[str, object]:
    if not isinstance(chunk, list):
        return {}
    return parse_usage_metadata(chunk[2] if len(chunk) > 2 else None)


def parse_response_chunk(chunk: list[object]) -> Candidate:
    candidate = Candidate()
    if (
        not isinstance(chunk, list)
        or not chunk
        or not isinstance(chunk[0], list)
        or not chunk[0]
    ):
        return candidate

    raw_candidate = chunk[0][0]
    if not isinstance(raw_candidate, list):
        return candidate

    raw_content = raw_candidate[0] if len(raw_candidate) > 0 else None
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
        raw_candidate[1]
        if len(raw_candidate) > 1 and isinstance(raw_candidate[1], int)
        else None
    )
    candidate.finish_message = (
        str(raw_candidate[3])
        if len(raw_candidate) > 3 and isinstance(raw_candidate[3], str)
        else ""
    )
    raw_ratings = raw_candidate[4] if len(raw_candidate) > 4 else None
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
    output.usage = parse_usage_metadata(last_chunk[2] if len(last_chunk) > 2 else None)
    output.response_id = (
        last_chunk[7] if len(last_chunk) > 7 and isinstance(last_chunk[7], str) else ""
    )
    output.candidates = [merged]
    return output


def parse_image_output(raw: str) -> ModelOutput:
    return parse_text_output(raw)


__all__ = [
    "ResponsePart",
    "parse_chunk_usage",
    "parse_image_output",
    "parse_response_chunk",
    "parse_response_part",
    "parse_text_output",
    "parse_usage_metadata",
]
