"""Google Protobuf wire array parser for AI Studio gateway responses.

Reverse-engineered Protobuf-over-JSON array specifications are documented in:
docs/WIRE_SPECIFICATION.md
"""

from __future__ import annotations

import base64
import json
import re
import struct

from aistudio_api.domain.errors import TransportError
from aistudio_api.domain.models import (
    Candidate,
    CandidatePart,
    GeneratedImage,
    ModelOutput,
)
from aistudio_api.infrastructure.gateway.wire_spec import (
    DECISION_USAGE_MODALITY_NAMES,
    WIRE_FINISH_REASON_CONTINUING,
    DecisionAnswerIndex,
)
from aistudio_api.infrastructure.utils.common import (
    decode_base64_images,
    extract_outer_json,
)

_ROLE_NAMES = frozenset({"model", "user", "system", "assistant"})

# ==============================================================================
# Google Protobuf-over-JSON Wire Array Specifications (docs/WIRE_SPECIFICATION.md)
# ==============================================================================
# Top-level chunk indexes (WIRE_SPECIFICATION.md §7.1)
WIRE_CHUNK_CANDIDATES_INDEX = 0  # Field 1 candidates container [[candidate, ...]]
WIRE_CHUNK_USAGE_INDEX = 2  # Field 3 usage metadata array
WIRE_CHUNK_PROMPT_FEEDBACK_INDEX = (
    1  # Field 2 PromptFeedback [blockReason, safetyRatings, blockReasonMessage]
)
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
WIRE_PART_VIDEO_METADATA_INDEX = 40  # Field 41 video metadata struct

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

# BlockReason -> FinishReason mapping from AI Studio frontend (dOb):
# 0: UNSPECIFIED -> 0, 1: SAFETY -> 3, 2: OTHER -> 5, 3: BLOCKLIST -> 7, 4: PROHIBITED_CONTENT -> 8, 5: IMAGE_SAFETY -> 11
_PROMPT_BLOCK_TO_FINISH_REASON: dict[int, int] = {
    0: 0,
    1: 3,
    2: 5,
    3: 7,
    4: 8,
    5: 11,
}

_STRING_FINISH_REASON_TO_CODE: dict[str, int] = {
    "FINISH_REASON_UNSPECIFIED": 0,
    "STOP": 1,
    "MAX_TOKENS": 2,
    "SAFETY": 3,
    "RECITATION": 4,
    "OTHER": 5,
    "LANGUAGE": 6,
    "BLOCKLIST": 7,
    "PROHIBITED_CONTENT": 8,
    "SPII": 9,
    "MALFORMED_FUNCTION_CALL": 10,
    "IMAGE_SAFETY": 11,
    "UNEXPECTED_TOOL_CALL": 12,
    "TOO_MANY_TOOL_CALLS": 13,
    "IMAGE_PROHIBITED_CONTENT": 14,
    "IMAGE_OTHER": 15,
    "NO_IMAGE": 16,
    "IMAGE_RECITATION": 17,
    "MISSING_THOUGHT_SIGNATURE": 18,
}


def _parse_finish_reason_value(raw_val: object) -> int | None:
    coerced = _coerce_int(raw_val)
    if coerced is not None:
        return coerced
    if isinstance(raw_val, str):
        key = raw_val.strip().upper()
        if key in _STRING_FINISH_REASON_TO_CODE:
            return _STRING_FINISH_REASON_TO_CODE[key]
    return None


_KNOWN_TERMINAL_FINISH_REASON_CODES: frozenset[int] = frozenset(
    code for code in _STRING_FINISH_REASON_TO_CODE.values() if code != 0
)


def is_terminal_wire_finish_reason(raw_val: object) -> bool:
    """Return True only for defined positive terminal finish_reason enum codes (1..18)."""
    if isinstance(raw_val, bool):
        return False
    if isinstance(raw_val, int):
        return raw_val in _KNOWN_TERMINAL_FINISH_REASON_CODES
    if isinstance(raw_val, str):
        stripped = raw_val.strip()
        if not stripped:
            return False
        if stripped.isdigit() or (stripped[0] in "+-" and stripped[1:].isdigit()):
            return int(stripped) in _KNOWN_TERMINAL_FINISH_REASON_CODES
        mapped = _STRING_FINISH_REASON_TO_CODE.get(stripped.upper())
        return mapped is not None and mapped in _KNOWN_TERMINAL_FINISH_REASON_CODES
    return False


_UPSTREAM_POLICY_BLOCK_RE = re.compile(
    r"(?:"
    r"the\s+prompt\s+could\s+not\s+be\s+submitted[\s\S]{0,300}?"
    r"(?:prohibited\s+use\s+policy|violate\s+google|sensitive\s+words)"
    r"|^\W*the\s+prompt\s+could\s+not\s+be\s+submitted\."
    r")",
    re.IGNORECASE,
)


def is_upstream_policy_block_text(text: str | None) -> bool:
    """Return True when upstream returned a pseudo-200 policy rejection message."""
    if not text:
        return False
    candidate = text.strip()
    if not candidate:
        return False
    return _UPSTREAM_POLICY_BLOCK_RE.match(candidate) is not None


def pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    bit_depth: int = 16,
) -> bytes:
    byte_rate = sample_rate * channels * (bit_depth // 8)
    block_align = channels * (bit_depth // 8)
    data_size = len(pcm_data)
    total_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        total_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bit_depth,
        b"data",
        data_size,
    )
    return header + pcm_data


def _is_pcm_or_wav_audio(mime: str) -> bool:
    lowered = mime.lower()
    return lowered in ("audio/wav", "audio/x-wav", "audio/l16", "audio/pcm") or (
        lowered.startswith("audio/") and "rate=24000" in lowered
    )


def merge_audio_images(images: list[GeneratedImage]) -> list[GeneratedImage]:
    if not images:
        return []
    result: list[GeneratedImage] = []
    current_audio_pcm: list[bytes] = []
    last_thought_sig = ""
    for item in images:
        if _is_pcm_or_wav_audio(item.mime):
            pcm = item.data[44:] if item.data.startswith(b"RIFF") else item.data
            current_audio_pcm.append(pcm)
            if item.thought_signature:
                last_thought_sig = item.thought_signature
        else:
            if current_audio_pcm:
                merged_wav = pcm_to_wav(b"".join(current_audio_pcm), sample_rate=24000)
                result.append(
                    GeneratedImage(
                        mime="audio/wav",
                        data=merged_wav,
                        size=len(merged_wav),
                        thought_signature=last_thought_sig,
                    )
                )
                current_audio_pcm = []
                last_thought_sig = ""
            result.append(item)
    if current_audio_pcm:
        merged_wav = pcm_to_wav(b"".join(current_audio_pcm), sample_rate=24000)
        result.append(
            GeneratedImage(
                mime="audio/wav",
                data=merged_wav,
                size=len(merged_wav),
                thought_signature=last_thought_sig,
            )
        )
    return result


def _merge_audio_candidate_parts(parts: list[CandidatePart]) -> list[CandidatePart]:
    if not parts:
        return []
    result: list[CandidatePart] = []
    pending_pcm: list[bytes] = []
    pending_kind = ""
    pending_thought = False
    pending_sig = ""
    pending_payload: dict[str, object] | None = None

    def _flush() -> None:
        nonlocal \
            pending_pcm, \
            pending_kind, \
            pending_thought, \
            pending_sig, \
            pending_payload
        if not pending_pcm:
            return
        merged_wav = pcm_to_wav(b"".join(pending_pcm), sample_rate=24000)
        img = GeneratedImage(
            mime="audio/wav",
            data=merged_wav,
            size=len(merged_wav),
            thought_signature=pending_sig,
        )
        result.append(
            CandidatePart(
                kind=pending_kind,
                thought=pending_thought,
                thought_signature=pending_sig,
                image=img,
                payload=pending_payload,
            )
        )
        pending_pcm = []
        pending_kind = ""
        pending_thought = False
        pending_sig = ""
        pending_payload = None

    for part in parts:
        if (
            part.kind in ("image", "reasoning_image")
            and part.image is not None
            and _is_pcm_or_wav_audio(part.image.mime)
        ):
            if pending_pcm and pending_kind != part.kind:
                _flush()
            pcm = (
                part.image.data[44:]
                if part.image.data.startswith(b"RIFF")
                else part.image.data
            )
            pending_kind = part.kind
            pending_thought = part.thought
            pending_pcm.append(pcm)
            if part.thought_signature:
                pending_sig = part.thought_signature
            elif part.image.thought_signature:
                pending_sig = part.image.thought_signature
            if part.payload is not None:
                pending_payload = part.payload
            continue
        _flush()
        result.append(part)
    _flush()
    return result


def _looks_like_response_chunk(value: object) -> bool:
    return isinstance(value, list) and (
        (len(value) > 0 and isinstance(value[0], list))
        or (len(value) > 1 and isinstance(value[1], list))
    )


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
        has_text: bool = False,
        inline_data: tuple[str, str] | None = None,
        file_data: dict[str, object] | None = None,
        video_metadata: dict[str, object] | None = None,
        thought: bool = False,
        function_call: dict[str, object] | None = None,
        function_response: dict[str, object] | None = None,
        thought_signature: str = "",
        executable_code: object | None = None,
        code_execution_result: object | None = None,
    ):
        self.text = text
        self.has_text = has_text
        self.inline_data = inline_data
        self.file_data = file_data
        self.video_metadata = video_metadata
        self.thought = thought
        self.function_call = function_call
        self.function_response = function_response
        self.thought_signature = thought_signature
        self.executable_code = executable_code
        self.code_execution_result = code_execution_result


def _parse_file_data_slot(raw_val: object) -> dict[str, object] | None:
    if isinstance(raw_val, dict):
        uri = raw_val.get("fileUri") or raw_val.get("file_uri")
        if isinstance(uri, str) and uri:
            out: dict[str, object] = {"fileUri": uri}
            mime = raw_val.get("mimeType") or raw_val.get("mime_type")
            if isinstance(mime, str) and mime:
                out["mimeType"] = mime
            return out
        return None
    if (
        isinstance(raw_val, list)
        and raw_val
        and isinstance(raw_val[0], str)
        and raw_val[0]
    ):
        out = {"fileUri": raw_val[0]}
        if len(raw_val) > 1 and isinstance(raw_val[1], str) and raw_val[1]:
            out["mimeType"] = raw_val[1]
        return out
    return None


def _parse_video_metadata_slot(raw_val: object) -> dict[str, object] | None:
    if not isinstance(raw_val, dict):
        return None
    out: dict[str, object] = {}
    start = raw_val.get("startOffset") or raw_val.get("start_offset")
    end = raw_val.get("endOffset") or raw_val.get("end_offset")
    fps = raw_val.get("fps")
    if isinstance(start, str) and start:
        out["startOffset"] = start
    if isinstance(end, str) and end:
        out["endOffset"] = end
    if isinstance(fps, (int, float)) and not isinstance(fps, bool):
        out["fps"] = float(fps)
    return out or None


def _coerce_executable_code_payload(raw_val: object) -> dict[str, object] | None:
    if raw_val is None:
        return None
    if isinstance(raw_val, dict):
        code = str(raw_val.get("code") or "")
        if not code:
            return None
        lang = str(raw_val.get("language") or "PYTHON")
        payload: dict[str, object] = {"language": lang, "code": code}
        if raw_val.get("id"):
            payload["id"] = str(raw_val["id"])
        return payload
    if isinstance(raw_val, list) and len(raw_val) > 1 and isinstance(raw_val[1], str):
        raw_lang = raw_val[0]
        if raw_lang in (1, "1", "PYTHON", "python"):
            lang = "PYTHON"
        elif isinstance(raw_lang, str) and raw_lang.strip():
            lang = raw_lang.strip()
        else:
            lang = "PYTHON"
        payload = {"language": lang, "code": raw_val[1]}
        if len(raw_val) > 2 and isinstance(raw_val[2], str) and raw_val[2]:
            payload["id"] = raw_val[2]
        return payload
    if isinstance(raw_val, str) and raw_val:
        return {"language": "PYTHON", "code": raw_val}
    return None


def _coerce_code_execution_result_payload(raw_val: object) -> dict[str, object] | None:
    if raw_val is None:
        return None
    outcomes = {
        1: "OUTCOME_OK",
        2: "OUTCOME_FAILED",
        3: "OUTCOME_DEADLINE_EXCEEDED",
    }
    if isinstance(raw_val, dict):
        output = str(raw_val.get("output") or "")
        raw_outcome = raw_val.get("outcome", "OUTCOME_OK")
        outcome = (
            outcomes.get(raw_outcome, str(raw_outcome))
            if isinstance(raw_outcome, int)
            else str(raw_outcome or "OUTCOME_OK")
        )
        payload: dict[str, object] = {"outcome": outcome, "output": output}
        if raw_val.get("id"):
            payload["id"] = str(raw_val["id"])
        return payload
    if isinstance(raw_val, list) and len(raw_val) > 1 and isinstance(raw_val[1], str):
        raw_outcome = raw_val[0]
        outcome = (
            outcomes.get(raw_outcome, str(raw_outcome))
            if isinstance(raw_outcome, int)
            else str(raw_outcome or "OUTCOME_OK")
        )
        payload = {"outcome": outcome, "output": raw_val[1]}
        if len(raw_val) > 2 and isinstance(raw_val[2], str) and raw_val[2]:
            payload["id"] = raw_val[2]
        return payload
    if isinstance(raw_val, str) and raw_val:
        return {"outcome": "OUTCOME_OK", "output": raw_val}
    return None


def parse_response_part(raw_part: object) -> ResponsePart:
    if not isinstance(raw_part, list):
        return ResponsePart()

    # A content block [parts, role] or nested list is not a part.
    if len(raw_part) > 0 and isinstance(raw_part[0], list):
        return ResponsePart()

    # Guard against role name in 2-element structure where index 1 is role.
    # Only treat as a content block when index 0 is a parts list or role name;
    # `[None, "model"]` is a valid text part whose text happens to be "model".
    if (
        len(raw_part) == 2
        and isinstance(raw_part[1], str)
        and raw_part[1] in _ROLE_NAMES
    ):
        first = raw_part[0]
        if isinstance(first, list) or (isinstance(first, str) and first in _ROLE_NAMES):
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

    file_data = (
        _parse_file_data_slot(raw_part[WIRE_PART_FILE_DATA_INDEX])
        if len(raw_part) > WIRE_PART_FILE_DATA_INDEX
        else None
    )
    video_metadata = (
        _parse_video_metadata_slot(raw_part[WIRE_PART_VIDEO_METADATA_INDEX])
        if len(raw_part) > WIRE_PART_VIDEO_METADATA_INDEX
        else None
    )

    function_call = _coerce_structural_payload(
        raw_part[WIRE_PART_FUNCTION_CALL_INDEX]
        if len(raw_part) > WIRE_PART_FUNCTION_CALL_INDEX
        else None,
        "functionCall",
    )
    if function_call is None and len(raw_part) > WIRE_PART_FUNCTION_CALL_ALT_INDEX:
        function_call = _coerce_structural_payload(
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

    function_response = _coerce_structural_payload(
        raw_part[WIRE_PART_FUNCTION_RESPONSE_INDEX]
        if len(raw_part) > WIRE_PART_FUNCTION_RESPONSE_INDEX
        else None,
        "functionResponse",
    )
    if (
        function_response is None
        and len(raw_part) > WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX
    ):
        function_response = _coerce_structural_payload(
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
    has_text = len(raw_part) > WIRE_PART_TEXT_INDEX and isinstance(
        raw_part[WIRE_PART_TEXT_INDEX], str
    )

    return ResponsePart(
        text=raw_part[WIRE_PART_TEXT_INDEX] if has_text else "",
        has_text=has_text,
        inline_data=inline_data,
        file_data=file_data,
        video_metadata=video_metadata,
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


def _coerce_structural_payload(
    raw_value: object, payload_type: str
) -> dict[str, object] | None:
    """Only accept structured (list/dict) payloads for functionCall/functionResponse.

    Index 10 is shared with WIRE_PART_THOUGHT_FLAG_INDEX (boolean True); scalar flags
    at index 10/11 must not be misinterpreted as function calls or responses.
    """
    if not isinstance(raw_value, (list, dict)):
        return None
    return _coerce_wire_payload(raw_value, payload_type)


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
    entries: list[object] = raw_struct
    while (
        len(entries) == 1
        and isinstance(entries[0], list)
        and not (len(entries[0]) >= 2 and isinstance(entries[0][0], str))
    ):
        entries = entries[0]

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
    if (
        prompt_tokens is None
        and visible_completion_tokens is None
        and total_tokens is None
        and cached_tokens is None
        and reasoning_tokens is None
    ):
        return {}
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


def parse_response_chunk(chunk: object) -> Candidate:
    candidate = Candidate()
    if not isinstance(chunk, list) or not chunk:
        return candidate

    candidates_container = (
        chunk[WIRE_CHUNK_CANDIDATES_INDEX]
        if len(chunk) > WIRE_CHUNK_CANDIDATES_INDEX
        else None
    )
    raw_candidate: list[object] | None = None
    if isinstance(candidates_container, list) and candidates_container:
        first_cand = candidates_container[0]
        if isinstance(first_cand, list):
            raw_candidate = first_cand

    if raw_candidate is not None:
        raw_content = (
            raw_candidate[WIRE_CANDIDATE_CONTENT]
            if len(raw_candidate) > WIRE_CANDIDATE_CONTENT
            else None
        )
        raw_parts = (
            raw_content[0]
            if isinstance(raw_content, list) and len(raw_content) > 0
            else []
        )

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        images: list[GeneratedImage] = []
        reasoning_images: list[GeneratedImage] = []
        function_calls: list[dict[str, object]] = []
        function_responses: list[dict[str, object]] = []
        thought_signature = ""
        thinking_signature = ""
        text_signature = ""
        standalone_signature = ""
        standalone_signatures: list[str] = []
        ordered_parts: list[CandidatePart] = []
        code_outputs: list[str] = []

        for raw_part in raw_parts if isinstance(raw_parts, list) else []:
            part = parse_response_part(raw_part)
            part_emitted_standalone_target = False
            if part.file_data is not None:
                ordered_parts.append(
                    CandidatePart(
                        kind="file_data",
                        thought=part.thought,
                        thought_signature=part.thought_signature,
                        payload=part.file_data,
                    )
                )
                part_emitted_standalone_target = True
            if part.inline_data:
                mime = part.inline_data[0]
                raw_b64 = part.inline_data[1]
                try:
                    raw_bytes = base64.b64decode(raw_b64)
                except Exception:
                    raw_bytes = b""
                mime_lower = mime.lower()
                vm_payload: dict[str, object] | None = (
                    {"video_metadata": part.video_metadata}
                    if part.video_metadata is not None
                    else None
                )
                if mime_lower.startswith("audio/"):
                    if (
                        mime_lower in ("audio/l16", "audio/pcm")
                        or "rate=24000" in mime_lower
                    ):
                        wav_bytes = pcm_to_wav(raw_bytes, sample_rate=24000)
                        media = GeneratedImage(
                            mime="audio/wav",
                            data=wav_bytes,
                            size=len(wav_bytes),
                            thought_signature=part.thought_signature or "",
                        )
                    else:
                        media = GeneratedImage(
                            mime=mime,
                            data=raw_bytes,
                            size=len(raw_bytes),
                            thought_signature=part.thought_signature or "",
                        )
                    target_list = reasoning_images if part.thought else images
                    target_list.append(media)
                    ordered_parts.append(
                        CandidatePart(
                            kind="reasoning_image" if part.thought else "image",
                            thought=part.thought,
                            thought_signature=part.thought_signature,
                            image=media,
                            payload=vm_payload,
                        )
                    )
                elif mime.startswith("video/"):
                    media = GeneratedImage(
                        mime=mime,
                        data=raw_bytes,
                        size=len(raw_bytes),
                        thought_signature=part.thought_signature or "",
                    )
                    target_list = reasoning_images if part.thought else images
                    target_list.append(media)
                    ordered_parts.append(
                        CandidatePart(
                            kind="reasoning_image" if part.thought else "image",
                            thought=part.thought,
                            thought_signature=part.thought_signature,
                            image=media,
                            payload=vm_payload,
                        )
                    )
                else:
                    decoded = decode_base64_images([{"mime": mime, "data": raw_b64}])
                    decoded_images: list[GeneratedImage] = []
                    for img in decoded:
                        raw_b = img.get("bytes")
                        b_data = (
                            bytes(raw_b)
                            if isinstance(raw_b, (bytes, bytearray))
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
                    target_list = reasoning_images if part.thought else images
                    target_list.extend(decoded_images)
                    for media in decoded_images:
                        ordered_parts.append(
                            CandidatePart(
                                kind="reasoning_image" if part.thought else "image",
                                thought=part.thought,
                                thought_signature=part.thought_signature,
                                image=media,
                                payload=vm_payload,
                            )
                        )
                part_emitted_standalone_target = True
            elif part.video_metadata is not None:
                ordered_parts.append(
                    CandidatePart(
                        kind="video_metadata",
                        thought=part.thought,
                        thought_signature=part.thought_signature,
                        payload=part.video_metadata,
                    )
                )
                part_emitted_standalone_target = True
            exec_payload = _coerce_executable_code_payload(part.executable_code)
            if exec_payload is not None:
                code_outputs.append(str(exec_payload["code"]))
                ordered_parts.append(
                    CandidatePart(
                        kind="executable_code",
                        thought=part.thought,
                        thought_signature=part.thought_signature,
                        payload=exec_payload,
                    )
                )
                part_emitted_standalone_target = True
            res_payload = _coerce_code_execution_result_payload(
                part.code_execution_result
            )
            if res_payload is not None:
                code_outputs.append(str(res_payload["output"]))
                ordered_parts.append(
                    CandidatePart(
                        kind="code_execution_result",
                        thought=part.thought,
                        thought_signature=part.thought_signature,
                        payload=res_payload,
                    )
                )
                part_emitted_standalone_target = True
            if part.function_call:
                function_calls.append(part.function_call)
                ordered_parts.append(
                    CandidatePart(
                        kind="function_call",
                        thought=part.thought,
                        thought_signature=part.thought_signature,
                        payload=part.function_call,
                    )
                )
                part_emitted_standalone_target = True
            if part.function_response:
                function_responses.append(part.function_response)
                ordered_parts.append(
                    CandidatePart(
                        kind="function_response",
                        thought=part.thought,
                        thought_signature=part.thought_signature,
                        payload=part.function_response,
                    )
                )
                part_emitted_standalone_target = True
            if part.text:
                if part.thought:
                    thinking_parts.append(part.text)
                    if part.thought_signature:
                        thinking_signature = part.thought_signature
                    ordered_parts.append(
                        CandidatePart(
                            kind="thinking",
                            text=part.text,
                            has_text=True,
                            thought=True,
                            thought_signature=part.thought_signature,
                        )
                    )
                else:
                    text_parts.append(part.text)
                    if part.thought_signature:
                        text_signature = part.thought_signature
                    ordered_parts.append(
                        CandidatePart(
                            kind="text",
                            text=part.text,
                            has_text=True,
                            thought_signature=part.thought_signature,
                        )
                    )
                part_emitted_standalone_target = True
            if part.thought_signature:
                thought_signature = part.thought_signature
                if not part_emitted_standalone_target:
                    standalone_signature = part.thought_signature
                    standalone_signatures.append(part.thought_signature)
                    ordered_parts.append(
                        CandidatePart(
                            kind="standalone_signature",
                            has_text=part.has_text,
                            thought=part.thought,
                            thought_signature=part.thought_signature,
                        )
                    )

        if ordered_parts:
            ordered_parts[0].chunk_start = True
            for idx, item in enumerate(ordered_parts):
                item.end_of_chunk = idx == len(ordered_parts) - 1

        candidate.text = "".join(text_parts)
        candidate.thinking = "".join(thinking_parts)
        candidate.images = images
        candidate.reasoning_images = reasoning_images
        candidate.function_calls = function_calls
        candidate.function_responses = function_responses
        candidate.thought_signature = thought_signature
        candidate.thinking_signature = thinking_signature
        candidate.text_signature = text_signature
        candidate.standalone_signature = standalone_signature
        candidate.standalone_signatures = standalone_signatures
        candidate.parts = ordered_parts
        candidate.code_output = "\n".join(code_outputs)
        candidate.finish_reason = (
            _parse_finish_reason_value(raw_candidate[WIRE_CANDIDATE_FINISH_REASON])
            if len(raw_candidate) > WIRE_CANDIDATE_FINISH_REASON
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

    # Parse PromptFeedback (chunk[1]: [blockReason, safetyRatings, blockReasonMessage])
    if len(chunk) > WIRE_CHUNK_PROMPT_FEEDBACK_INDEX:
        fb = chunk[WIRE_CHUNK_PROMPT_FEEDBACK_INDEX]
        if isinstance(fb, list):
            block_code = _coerce_int(fb[0]) if len(fb) > 0 else None
            if block_code is not None and candidate.finish_reason in (None, 0):
                candidate.finish_reason = _PROMPT_BLOCK_TO_FINISH_REASON.get(
                    block_code, block_code
                )
            if len(fb) > 1 and isinstance(fb[1], list) and not candidate.safety_ratings:
                candidate.safety_ratings = fb[1]
            if (
                len(fb) > 2
                and isinstance(fb[2], str)
                and fb[2]
                and not candidate.finish_message
            ):
                candidate.finish_message = fb[2]
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
        usage = parse_chunk_usage(chunk)
        if usage:
            output.usage = usage
        if len(chunk) > WIRE_RESPONSE_ID_INDEX:
            raw_rid = chunk[WIRE_RESPONSE_ID_INDEX]
            if isinstance(raw_rid, str) and raw_rid:
                output.response_id = raw_rid
        parsed = parse_response_chunk(chunk)
        if parsed.parts:
            for part in parsed.parts:
                if (
                    merged.parts
                    and part.kind in ("text", "thinking")
                    and merged.parts[-1].kind == part.kind
                    and not merged.parts[-1].thought_signature
                ):
                    merged.parts[-1].text += part.text
                    if part.thought_signature:
                        merged.parts[-1].thought_signature = part.thought_signature
                    merged.parts[-1].end_of_chunk = part.end_of_chunk
                else:
                    merged.parts.append(part)
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
        if parsed.thinking_signature:
            merged.thinking_signature = parsed.thinking_signature
        if parsed.text_signature:
            merged.text_signature = parsed.text_signature
        if parsed.standalone_signature:
            merged.standalone_signature = parsed.standalone_signature
        if parsed.standalone_signatures:
            merged.standalone_signatures.extend(parsed.standalone_signatures)
        if parsed.thought_signature:
            merged.thought_signature = parsed.thought_signature
        if parsed.code_output:
            merged.code_output = "\n".join(
                filter(None, [merged.code_output, parsed.code_output])
            )
        if parsed.finish_reason is not None and parsed.finish_reason not in (
            0,
            WIRE_FINISH_REASON_CONTINUING,
        ):
            merged.finish_reason = parsed.finish_reason
        if parsed.finish_message:
            merged.finish_message = parsed.finish_message
        if parsed.safety_ratings:
            merged.safety_ratings = parsed.safety_ratings
    merged.parts = _merge_audio_candidate_parts(merged.parts)
    merged.images = merge_audio_images(merged.images)
    merged.reasoning_images = merge_audio_images(merged.reasoning_images)
    output.candidates = [merged]
    return output


def _parse_auxiliary_rpc_array(raw_text: str, rpc_name: str) -> list[object]:
    """读取辅助 RPC 的完整 JSON 数组，并保留标准 XSSI 前缀支持。"""
    payload = raw_text.strip()
    if payload.startswith(")]}'"):
        payload = payload[4:].lstrip()
    try:
        outer: object = json.loads(payload)
    except json.JSONDecodeError:
        raise TransportError(f"{rpc_name} 返回不可解析的 JSON。") from None
    if not isinstance(outer, list):
        raise TransportError(f"{rpc_name} 响应顶层不是数组。")
    return outer


def parse_decision_response(raw_text: str) -> dict[str, object]:
    """Parse MakerSuiteService/CreateDecision JSPB response into Decisions API JSON."""
    outer = _parse_auxiliary_rpc_array(raw_text, "CreateDecision")
    if any(value is not None and not isinstance(value, list) for value in outer[:2]):
        raise TransportError("CreateDecision 响应顶层字段不是数组。")
    answers: dict[str, dict[str, object]] = {}
    raw_answers = outer[0] if len(outer) > 0 and isinstance(outer[0], list) else []
    for entry in raw_answers:
        if (
            not isinstance(entry, list)
            or len(entry) < 2
            or not isinstance(entry[0], str)
        ):
            continue
        q_key = entry[0]
        ans = entry[1]
        if not isinstance(ans, list):
            continue
        raw_conf = (
            ans[DecisionAnswerIndex.CONFIDENCE]
            if len(ans) > DecisionAnswerIndex.CONFIDENCE
            else None
        )
        confidence = (
            float(raw_conf)
            if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool)
            else 0.0
        )

        noul_detail = (
            ans[DecisionAnswerIndex.NOUL_DETAIL]
            if len(ans) > DecisionAnswerIndex.NOUL_DETAIL
            else None
        )
        choice_detail = (
            ans[DecisionAnswerIndex.CHOICE_DETAIL]
            if len(ans) > DecisionAnswerIndex.CHOICE_DETAIL
            else None
        )
        score_detail = (
            ans[DecisionAnswerIndex.SCORE_DETAIL]
            if len(ans) > DecisionAnswerIndex.SCORE_DETAIL
            else None
        )

        if isinstance(noul_detail, list) and len(noul_detail) >= 2:
            raw_noul = noul_detail[1]
            noul_prob = (
                float(raw_noul)
                if isinstance(raw_noul, (int, float)) and not isinstance(raw_noul, bool)
                else confidence
            )
            answers[q_key] = {
                "type": "noul",
                "noul": noul_prob,
                "confidence": confidence,
            }
        elif isinstance(choice_detail, list) and len(choice_detail) >= 2:
            top_choice = str(choice_detail[0]) if choice_detail[0] is not None else ""
            probs: dict[str, float] = {}
            if isinstance(choice_detail[1], list):
                for pair in choice_detail[1]:
                    if (
                        isinstance(pair, list)
                        and len(pair) >= 2
                        and isinstance(pair[1], (int, float))
                    ):
                        probs[str(pair[0])] = float(pair[1])
            answers[q_key] = {
                "type": "choice",
                "choice": top_choice,
                "probabilities": probs,
                "confidence": confidence,
            }
        elif isinstance(score_detail, list) and len(score_detail) >= 1:
            raw_score = score_detail[0]
            score_val = (
                float(raw_score)
                if isinstance(raw_score, (int, float))
                and not isinstance(raw_score, bool)
                else 0.0
            )
            probs = {}
            if len(score_detail) > 3 and isinstance(score_detail[3], list):
                for pair in score_detail[3]:
                    if (
                        isinstance(pair, list)
                        and len(pair) >= 2
                        and isinstance(pair[1], (int, float))
                    ):
                        probs[str(pair[0])] = float(pair[1])
            legend: dict[str, str] = {}
            if len(score_detail) > 4 and isinstance(score_detail[4], list):
                for pair in score_detail[4]:
                    if isinstance(pair, list) and len(pair) >= 2:
                        legend[str(pair[0])] = str(pair[1])
            answers[q_key] = {
                "type": "score",
                "score": score_val,
                "legend": legend,
                "probabilities": probs,
                "confidence": confidence,
            }

    usage_raw = outer[1] if len(outer) > 1 and isinstance(outer[1], list) else []
    total_in = _coerce_int(usage_raw[0] if len(usage_raw) > 0 else None) or 0
    total_tok = _coerce_int(usage_raw[9] if len(usage_raw) > 9 else None)
    if total_tok is None:
        total_tok = total_in
    by_modality: list[dict[str, object]] = []
    if len(usage_raw) > 1 and isinstance(usage_raw[1], list):
        for mod_entry in usage_raw[1]:
            if isinstance(mod_entry, list) and len(mod_entry) >= 2:
                mod_code = _coerce_int(mod_entry[0])
                mod_tokens = _coerce_int(mod_entry[1]) or 0
                mod_name = DECISION_USAGE_MODALITY_NAMES.get(
                    mod_code or 0, f"unknown_{mod_code}"
                )
                by_modality.append({"modality": mod_name, "tokens": mod_tokens})

    return {
        "answers": answers,
        "usage": {
            "total_input_tokens": total_in,
            "input_tokens_by_modality": by_modality,
            "total_output_tokens": 0,
            "total_tokens": total_tok,
        },
    }


def parse_count_tokens_response(raw_text: str) -> dict[str, object]:
    """Parse MakerSuiteService/CountTokens JSPB response."""
    outer = _parse_auxiliary_rpc_array(raw_text, "CountTokens")
    raw_total = outer[0] if outer else None
    total = _coerce_int(raw_total)
    if raw_total is not None and total is None:
        raise TransportError("CountTokens 响应 token 总数不是整数。")
    result: dict[str, object] = {"totalTokens": total or 0}
    cached = _coerce_int(outer[3] if len(outer) > 3 else None)
    if cached is not None:
        result["cachedContentTokenCount"] = cached
    modality_map = {1: "TEXT", 2: "IMAGE", 3: "AUDIO", 4: "VIDEO", 5: "DOCUMENT"}
    if len(outer) > 5 and isinstance(outer[5], list):
        details: list[dict[str, object]] = []
        for entry in outer[5]:
            if isinstance(entry, list) and len(entry) >= 2:
                m_code = _coerce_int(entry[0])
                t_cnt = _coerce_int(entry[1])
                if m_code is not None and t_cnt is not None:
                    details.append(
                        {
                            "modality": modality_map.get(
                                m_code, "MODALITY_UNSPECIFIED"
                            ),
                            "tokenCount": t_cnt,
                        }
                    )
        if details:
            result["promptTokensDetails"] = details
    return result


__all__ = [
    "WIRE_CANDIDATE_CONTENT",
    "WIRE_CANDIDATE_FINISH_MESSAGE",
    "WIRE_CANDIDATE_FINISH_REASON",
    "WIRE_CANDIDATE_SAFETY_RATINGS",
    "WIRE_CHUNK_CANDIDATES_INDEX",
    "WIRE_CHUNK_PROMPT_FEEDBACK_INDEX",
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
    "WIRE_PART_VIDEO_METADATA_INDEX",
    "WIRE_RESPONSE_ID_INDEX",
    "WIRE_USAGE_CACHED_TOKENS",
    "WIRE_USAGE_PROMPT_DETAILS",
    "WIRE_USAGE_PROMPT_TOKENS",
    "WIRE_USAGE_REASONING_TOKENS",
    "WIRE_USAGE_TOTAL_TOKENS",
    "WIRE_USAGE_VISIBLE_COMPLETION",
    "ResponsePart",
    "is_terminal_wire_finish_reason",
    "is_upstream_policy_block_text",
    "parse_chunk_usage",
    "parse_count_tokens_response",
    "parse_decision_response",
    "parse_response_chunk",
    "parse_response_part",
    "parse_text_output",
    "parse_usage_metadata",
]
