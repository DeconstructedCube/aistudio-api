"""Codec between typed request objects and reverse-engineered AI Studio wire arrays."""

from __future__ import annotations

import json
from collections.abc import Sequence

from aistudio_api.config import DEFAULT_TEXT_MODEL, settings

from .model_defaults import resolve_model_defaults
from .wire_parser import (
    WIRE_PART_FILE_DATA_INDEX,
    parse_response_part,
)
from .wire_spec import (
    INTERACTIONS_AUDIO_MIME_TYPES,
    INTERACTIONS_IMAGE_MIME_TYPES,
    INTERACTIONS_VIDEO_MIME_TYPES,
    CreateDecisionIndex,
    DecisionQuestionIndex,
    GenerationConfigIndex,
    InteractionsContentIndex,
    JspbArray,
    TopLevelIndex,
)
from .wire_types import (
    AistudioContent,
    AistudioGenerationConfig,
    AistudioPart,
    AistudioRequest,
)

TOOLS_TEMPLATES: dict[str, list[object]] = {
    "code_execution": [[]],
    "google_search": [None, None, None, [None, [[]]]],
    "google_maps": [None, None, None, None, None, None, None, None, None, None, []],
    "url_context": [None, None, None, None, None, None, None, []],
}


def build_image_generation_search_tool(
    *, google_search: bool = False, image_search: bool = False
) -> list[object] | None:
    if not google_search and not image_search:
        return None
    if google_search and image_search:
        return [None, None, None, [None, [[], []]]]
    if google_search:
        return TOOLS_TEMPLATES["google_search"]
    return [None, None, None, [None, [None, []]]]


def _normalize_tool_name(raw_name: str, *, is_image_model: bool = False) -> str:
    name = str(raw_name).strip().lower()
    if is_image_model:
        if name in {
            "google_search_and_image_search",
            "image_google_search_and_image_search",
        }:
            return "google_search_and_image_search"
        if name in {"image_search", "google_image_search"}:
            return "image_search"
        if name in {"google_search", "image_google_search"}:
            return "google_search"
    return name


def _allowed_builtin_tools_for_model(
    model: str | None, *, is_image_model: bool = False
) -> set[str]:
    if is_image_model:
        return {"google_search", "image_search", "google_search_and_image_search"}
    normalized_model = str(model or "").removeprefix("models/").lower()
    if normalized_model.startswith("gemma-"):
        allowed = {"google_search", "code_execution"}
    elif normalized_model.startswith("gemini-"):
        allowed = {"google_search", "code_execution", "google_maps", "url_context"}
    else:
        allowed = {"google_search", "code_execution"}

    if normalized_model:
        try:
            from .model_discovery import model_discovery

            info = model_discovery.get_cached_model_info(normalized_model)
            if info:
                caps = info.get("capabilities")
                if isinstance(caps, list):
                    for cap in caps:
                        if cap in (
                            "google_search",
                            "code_execution",
                            "google_maps",
                            "url_context",
                        ):
                            allowed.add(str(cap))
        except Exception:
            pass
    return allowed


def build_tools_from_names(
    tool_names: list[str] | tuple[str, ...],
    *,
    model: str | None = None,
    is_image_model: bool = False,
    drop_unsupported: bool = False,
) -> list[list]:
    allowed = _allowed_builtin_tools_for_model(model, is_image_model=is_image_model)
    if is_image_model:
        google_search = False
        image_search = False
        for raw_name in tool_names:
            name = _normalize_tool_name(raw_name, is_image_model=True)
            if not name:
                continue
            if name not in allowed:
                if drop_unsupported:
                    continue
                raise ValueError(
                    f"Tool {raw_name!r} is not allowed for model {model or 'unknown'}"
                )
            if name == "google_search_and_image_search":
                google_search = True
                image_search = True
            elif name == "google_search":
                google_search = True
            elif name == "image_search":
                image_search = True
        tool = build_image_generation_search_tool(
            google_search=google_search,
            image_search=image_search,
        )
        return [tool] if tool is not None else []

    tools: list[list[object]] = []
    for raw_name in tool_names:
        name = _normalize_tool_name(raw_name, is_image_model=False)
        if not name:
            continue
        if name not in allowed:
            if drop_unsupported:
                continue
            raise ValueError(
                f"Tool {raw_name!r} is not allowed for model {model or 'unknown'}"
            )
        if name not in TOOLS_TEMPLATES:
            if drop_unsupported:
                continue
            raise ValueError(f"Unsupported tool name: {raw_name!r}")
        tools.append(TOOLS_TEMPLATES[name])
    return tools


class AistudioWireCodec:
    MODEL_INDEX = TopLevelIndex.MODEL
    CONTENTS_INDEX = TopLevelIndex.CONTENTS
    SAFETY_INDEX = TopLevelIndex.SAFETY_SETTINGS
    GENERATION_CONFIG_INDEX = TopLevelIndex.GENERATION_CONFIG
    SNAPSHOT_INDEX = TopLevelIndex.SNAPSHOT
    SYSTEM_INSTRUCTION_INDEX = TopLevelIndex.SYSTEM_INSTRUCTION
    TOOLS_INDEX = TopLevelIndex.TOOLS
    EVERGREEN_MODEL_URI_INDEX = TopLevelIndex.EVERGREEN_MODEL_URI
    REQUEST_FLAG_INDEX = TopLevelIndex.REQUEST_FLAG
    SESSION_TOKEN_INDEX = TopLevelIndex.SESSION_TOKEN
    TIMEZONE_INDEX = TopLevelIndex.REQUEST_CONTEXT

    def decode(self, raw_body: str = "[]") -> AistudioRequest:
        parsed = json.loads(raw_body)
        if not isinstance(parsed, list):
            raise ValueError(
                f"wire body must be a JSON array, got {type(parsed).__name__}"
            )
        body = list(parsed)
        self._ensure_len(body, self.TIMEZONE_INDEX + 1)
        raw_gc = body[self.GENERATION_CONFIG_INDEX]
        return AistudioRequest(
            model=str(body[self.MODEL_INDEX] or DEFAULT_TEXT_MODEL),
            contents=self._decode_contents(body[self.CONTENTS_INDEX]),
            safety_settings=body[self.SAFETY_INDEX],
            generation_config=AistudioGenerationConfig(
                list(raw_gc) if isinstance(raw_gc, list) else []
            ),
            snapshot=body[self.SNAPSHOT_INDEX]
            if len(body) > self.SNAPSHOT_INDEX
            else None,
            system_instruction=self._decode_system_instruction(
                body[self.SYSTEM_INSTRUCTION_INDEX]
                if len(body) > self.SYSTEM_INSTRUCTION_INDEX
                else None
            ),
            tools=body[self.TOOLS_INDEX] if len(body) > self.TOOLS_INDEX else None,
            evergreen_model_uri=body[self.EVERGREEN_MODEL_URI_INDEX]
            if len(body) > self.EVERGREEN_MODEL_URI_INDEX
            and isinstance(body[self.EVERGREEN_MODEL_URI_INDEX], str)
            else None,
            request_flag=body[self.REQUEST_FLAG_INDEX]
            if len(body) > self.REQUEST_FLAG_INDEX
            else None,
            session_token=body[self.SESSION_TOKEN_INDEX]
            if len(body) > self.SESSION_TOKEN_INDEX
            else None,
            location=body[self.TIMEZONE_INDEX]
            if len(body) > self.TIMEZONE_INDEX
            else None,
            raw_body=body,
        )

    def encode(self, request: AistudioRequest) -> str:
        body = list(request.raw_body)
        self._ensure_len(body, self.SNAPSHOT_INDEX + 1)

        model = request.model
        if not model.startswith("models/"):
            model = f"models/{model}"
        body[self.MODEL_INDEX] = model
        body[self.CONTENTS_INDEX] = [content.to_wire() for content in request.contents]
        body[self.SAFETY_INDEX] = request.safety_settings
        body[self.GENERATION_CONFIG_INDEX] = request.generation_config.values
        if "argon" not in model.lower() and isinstance(
            body[self.GENERATION_CONFIG_INDEX], list
        ):
            gc_vals = body[self.GENERATION_CONFIG_INDEX]
            idx_45 = GenerationConfigIndex.ARGON_CAPABILITY_FLAG
            if len(gc_vals) > idx_45 and gc_vals[idx_45] is not None:
                gc_vals = list(gc_vals)
                gc_vals[idx_45] = None
                while gc_vals and gc_vals[-1] is None:
                    gc_vals.pop()
                body[self.GENERATION_CONFIG_INDEX] = gc_vals
        body[self.SNAPSHOT_INDEX] = request.snapshot

        if request.system_instruction:
            self._ensure_len(body, self.SYSTEM_INSTRUCTION_INDEX + 1)
            body[self.SYSTEM_INSTRUCTION_INDEX] = request.system_instruction.to_wire()
        if request.tools:
            self._ensure_len(body, self.TOOLS_INDEX + 1)
            body[self.TOOLS_INDEX] = request.tools
        if request.evergreen_model_uri:
            self._ensure_len(body, self.EVERGREEN_MODEL_URI_INDEX + 1)
            body[self.EVERGREEN_MODEL_URI_INDEX] = request.evergreen_model_uri
        if request.request_flag is not None:
            self._ensure_len(body, self.REQUEST_FLAG_INDEX + 1)
            body[self.REQUEST_FLAG_INDEX] = request.request_flag
        if request.session_token is not None:
            self._ensure_len(body, self.SESSION_TOKEN_INDEX + 1)
            body[self.SESSION_TOKEN_INDEX] = request.session_token

        model_defaults = resolve_model_defaults(request.model)
        if model_defaults.disable_safety_settings:
            body[self.SAFETY_INDEX] = None
        if not model_defaults.is_image_model:
            if request.tools or request.location is not None:
                self._ensure_len(body, self.TIMEZONE_INDEX + 1)
                tz_str = settings.timezone
                body[self.TIMEZONE_INDEX] = request.location or [
                    [None, None, tz_str],
                    None,
                    1,
                ]
            else:
                while len(body) > self.SNAPSHOT_INDEX + 1 and body[-1] is None:
                    body.pop()
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    def build_request(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        snapshot: str | None = None,
        prompt: str | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction: str | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        images: list[tuple[str, str]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
    ) -> AistudioRequest:
        effective_contents = (
            contents
            if contents is not None
            else [AistudioContent.from_user_prompt(prompt=prompt or "", images=images)]
        )
        effective_sys = (
            system_instruction_content
            if system_instruction_content is not None
            else (
                AistudioContent(
                    role="user", parts=[AistudioPart(text=system_instruction)]
                )
                if system_instruction
                else None
            )
        )
        model_defaults = resolve_model_defaults(model)
        gc = AistudioGenerationConfig()
        defaults = model_defaults.generation_config_overrides()
        for attr, value in defaults.items():
            setattr(gc, attr, value)
        for idx in model_defaults.clear_generation_config_indexes:
            gc._set_slot(idx, None)

        effective_safety = (
            safety_settings
            if safety_settings is not None
            else model_defaults.safety_settings_overrides()
        )

        if max_tokens is not None:
            gc.max_tokens = max_tokens
        if temperature is not None:
            gc.temperature = temperature
        if top_p is not None:
            gc.top_p = top_p
        if top_k is not None:
            gc.top_k = top_k
        for attr, value in (generation_config_overrides or {}).items():
            if not hasattr(gc, attr):
                continue
            setattr(gc, attr, value)
        if "argon" not in model.lower():
            gc._set_slot(GenerationConfigIndex.ARGON_CAPABILITY_FLAG, None)
        effective_tools: list[list] | None = tools if tools else None
        if tool_config is not None:
            is_none_mode = False
            allowed_names: list[str] | None = None
            if (
                isinstance(tool_config, list)
                and len(tool_config) > 1
                and isinstance(tool_config[1], list)
            ):
                if tool_config[1] and tool_config[1][0] == 3:
                    is_none_mode = True
                elif (
                    tool_config[1]
                    and len(tool_config[1]) > 1
                    and isinstance(tool_config[1][1], list)
                ):
                    allowed_names = [str(x) for x in tool_config[1][1]]
            elif isinstance(tool_config, dict):
                fcc = tool_config.get("functionCallingConfig") or tool_config.get(
                    "function_calling_config"
                )
                if isinstance(fcc, dict):
                    if fcc.get("mode") in (3, "NONE", "none"):
                        is_none_mode = True
                    raw_allowed = fcc.get("allowedFunctionNames") or fcc.get(
                        "allowed_function_names"
                    )
                    if isinstance(raw_allowed, list):
                        allowed_names = [str(x) for x in raw_allowed]
            if is_none_mode:
                effective_tools = None
            elif allowed_names is not None and effective_tools:
                filtered_tools: list[list[object]] = []
                allowed_set = set(allowed_names)
                for t in effective_tools:
                    if isinstance(t, list) and len(t) > 1 and isinstance(t[1], list):
                        filtered_decls = [
                            d
                            for d in t[1]
                            if isinstance(d, list) and d and d[0] in allowed_set
                        ]
                        if filtered_decls:
                            filtered_tools.append([t[0], filtered_decls])
                    else:
                        filtered_tools.append(t)
                effective_tools = filtered_tools or None

        return AistudioRequest(
            model=model,
            contents=effective_contents,
            safety_settings=effective_safety,
            generation_config=gc,
            snapshot=snapshot,
            system_instruction=effective_sys,
            tools=effective_tools,
        )

    def _decode_contents(self, raw_contents: object) -> list[AistudioContent]:
        contents: list[AistudioContent] = []
        if not isinstance(raw_contents, list):
            return contents
        for item in raw_contents:
            if not isinstance(item, list) or len(item) < 2:
                continue
            raw_parts, role = item[0], item[1]
            parts = []
            if isinstance(raw_parts, list):
                for raw_part in raw_parts:
                    parts.append(self._decode_part(raw_part))
            contents.append(AistudioContent(role=role, parts=parts))
        return contents

    def _decode_part(self, raw_part) -> AistudioPart:
        parsed = parse_response_part(raw_part)
        part = AistudioPart(
            thought=parsed.thought,
            thought_signature=parsed.thought_signature or None,
        )
        if not isinstance(raw_part, list):
            return part
        if (
            len(raw_part) > WIRE_PART_FILE_DATA_INDEX
            and isinstance(raw_part[WIRE_PART_FILE_DATA_INDEX], list)
            and raw_part[WIRE_PART_FILE_DATA_INDEX]
            and isinstance(raw_part[WIRE_PART_FILE_DATA_INDEX][0], str)
        ):
            part.file_id = raw_part[WIRE_PART_FILE_DATA_INDEX][0]
        elif parsed.inline_data is not None:
            part.inline_data = parsed.inline_data
        elif parsed.function_call is not None:
            raw = parsed.function_call.get("raw")
            if isinstance(raw, list):
                name = raw[0] if raw and isinstance(raw[0], str) else "unknown"
                args = raw[1] if len(raw) > 1 else {}
                part.function_call = (
                    (name, args, raw[2])
                    if len(raw) > 2 and isinstance(raw[2], str)
                    else (name, args)
                )
        elif parsed.function_response is not None:
            raw = parsed.function_response.get("raw")
            if isinstance(raw, list):
                name = raw[0] if raw and isinstance(raw[0], str) else "unknown"
                response = raw[1] if len(raw) > 1 else {}
                part.function_response = (
                    (name, response, raw[2])
                    if len(raw) > 2 and isinstance(raw[2], str)
                    else (name, response)
                )
        elif (
            isinstance(parsed.executable_code, list) and len(parsed.executable_code) > 1
        ):
            part.executable_code = (
                str(parsed.executable_code[0]),
                str(parsed.executable_code[1]),
            )
        elif (
            isinstance(parsed.code_execution_result, list)
            and len(parsed.code_execution_result) > 1
        ):
            outcomes = {
                1: "OUTCOME_OK",
                2: "OUTCOME_FAILED",
                3: "OUTCOME_DEADLINE_EXCEEDED",
            }
            raw_outcome = parsed.code_execution_result[0]
            outcome_str = (
                outcomes.get(raw_outcome, str(raw_outcome))
                if isinstance(raw_outcome, int)
                else str(raw_outcome)
            )
            part.code_execution_result = (
                outcome_str,
                str(parsed.code_execution_result[1]),
            )
        else:
            part.text = parsed.text
        return part

    def _decode_system_instruction(self, raw_instruction) -> AistudioContent | None:
        if not raw_instruction or not isinstance(raw_instruction, list):
            return None
        if len(raw_instruction) >= 2 and isinstance(raw_instruction[1], str):
            decoded = self._decode_contents([raw_instruction])
            return decoded[0] if decoded else None
        parts = [
            self._decode_part(raw_part)
            for raw_part in raw_instruction
            if isinstance(raw_part, list)
        ]
        if not parts:
            return None
        return AistudioContent(role="user", parts=parts)

    def _ensure_len(self, body: list, size: int):
        while len(body) < size:
            body.append(None)


_codec = AistudioWireCodec()


def encode_request_body(
    *,
    model: str = DEFAULT_TEXT_MODEL,
    snapshot: str | None = None,
    prompt: str | None = None,
    contents: list[AistudioContent] | None = None,
    system_instruction: str | None = None,
    system_instruction_content: AistudioContent | None = None,
    tools: list[list] | None = None,
    tool_config: list[object] | None = None,
    safety_settings: list[list] | None = None,
    images: list[tuple[str, str]] | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    max_tokens: int | None = None,
    generation_config_overrides: dict | None = None,
) -> str:
    request = _codec.build_request(
        model=model,
        snapshot=snapshot,
        prompt=prompt,
        contents=contents,
        system_instruction=system_instruction,
        system_instruction_content=system_instruction_content,
        tools=tools,
        tool_config=tool_config,
        safety_settings=safety_settings,
        images=images,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        max_tokens=max_tokens,
        generation_config_overrides=generation_config_overrides,
    )
    return _codec.encode(request)


def _normalize_interactions_blocks(
    raw_input: str | dict[str, object] | Sequence[object],
) -> list[dict[str, object]]:
    if isinstance(raw_input, str):
        if not raw_input:
            raise ValueError("input must not be empty")
        return [{"type": "text", "text": raw_input}]
    if isinstance(raw_input, dict):
        return [raw_input]
    blocks = [item for item in raw_input if isinstance(item, dict)]
    if not blocks:
        raise ValueError("input must contain at least one content block")
    return blocks


def _encode_interactions_block(block: dict[str, object]) -> list[object]:
    block_type = str(block.get("type") or "").strip().lower()
    if not block_type and "text" in block:
        block_type = "text"

    wire = JspbArray()
    if block_type == "text":
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError("text content block requires string 'text'")
        wire[InteractionsContentIndex.TEXT] = [text, None]
        return wire.compact()

    mime_type = (
        str(block.get("mime_type") or block.get("mimeType") or "").strip().lower()
    )
    data = block.get("data")
    uri = block.get("uri")
    if not isinstance(data, str) or not data:
        data = None
    if not isinstance(uri, str) or not uri:
        uri = None
    if data is None and uri is None:
        raise ValueError(
            f"{block_type or 'media'} content block requires 'data' or 'uri'"
        )

    if block_type == "image":
        mime_code = INTERACTIONS_IMAGE_MIME_TYPES.get(mime_type, 0)
        media_wire = JspbArray([mime_code])
        if data is not None:
            media_wire[1] = data
        if uri is not None:
            media_wire[2] = uri
        wire[InteractionsContentIndex.IMAGE] = media_wire.compact()
        return wire.compact()

    if block_type == "audio":
        mime_code = INTERACTIONS_AUDIO_MIME_TYPES.get(mime_type, 0)
        media_wire = JspbArray([mime_code])
        if data is not None:
            media_wire[1] = data
        if uri is not None:
            media_wire[2] = uri
        wire[InteractionsContentIndex.AUDIO] = media_wire.compact()
        return wire.compact()

    if block_type == "document":
        mime_code = 1 if mime_type == "application/pdf" else 0
        media_wire = JspbArray([mime_code])
        if data is not None:
            media_wire[1] = data
        if uri is not None:
            media_wire[2] = uri
        wire[InteractionsContentIndex.DOCUMENT] = media_wire.compact()
        return wire.compact()

    if block_type == "video":
        mime_code = INTERACTIONS_VIDEO_MIME_TYPES.get(mime_type, 0)
        media_wire = JspbArray([mime_code])
        if data is not None:
            media_wire[1] = data
        if uri is not None:
            media_wire[2] = uri
        wire[InteractionsContentIndex.VIDEO] = media_wire.compact()
        return wire.compact()

    raise ValueError(f"Unsupported Decisions API input block type: {block_type!r}")


def extract_decision_digest_contents(
    raw_input: str | dict[str, object] | Sequence[object],
) -> list[AistudioContent]:
    """Extract AistudioContent parts from Decisions input for WAA SHA-256 digest."""
    blocks = _normalize_interactions_blocks(raw_input)
    parts: list[AistudioPart] = []
    for block in blocks:
        block_type = str(block.get("type") or "").strip().lower()
        if not block_type and "text" in block:
            block_type = "text"
        if block_type == "text":
            parts.append(AistudioPart(text=str(block.get("text") or "")))
        elif isinstance(block.get("data"), str) and block["data"]:
            mime_type = str(
                block.get("mime_type")
                or block.get("mimeType")
                or "application/octet-stream"
            )
            parts.append(AistudioPart(inline_data=(mime_type, str(block["data"]))))
        elif isinstance(block.get("uri"), str) and block["uri"]:
            parts.append(AistudioPart(file_id=str(block["uri"])))
    return [AistudioContent(role="user", parts=parts or [AistudioPart(text="")])]


def _encode_decision_question(
    question: dict[str, object],
) -> list[object]:
    q_type = str(question.get("type") or "").strip().lower()
    raw_instr = question.get("instructions")
    if isinstance(raw_instr, str):
        instr_blocks: list[dict[str, object]] = [{"type": "text", "text": raw_instr}]
    elif isinstance(raw_instr, dict):
        instr_blocks = [raw_instr]
    elif isinstance(raw_instr, list):
        instr_blocks = [b for b in raw_instr if isinstance(b, dict)]
    else:
        raise ValueError("question.instructions is required")
    if not instr_blocks:
        raise ValueError("question.instructions must not be empty")

    instr_wire = [_encode_interactions_block(b) for b in instr_blocks]
    q_wire = JspbArray()
    q_wire[DecisionQuestionIndex.INSTRUCTIONS] = instr_wire

    criteria = question.get("criteria")
    if q_type == "noul":
        if isinstance(criteria, dict):
            true_desc = str(
                criteria.get("true_description")
                or criteria.get("trueDescription")
                or ""
            )
            false_desc = str(
                criteria.get("false_description")
                or criteria.get("falseDescription")
                or ""
            )
            q_wire[DecisionQuestionIndex.NOUL] = [[true_desc, false_desc]]
        else:
            q_wire[DecisionQuestionIndex.NOUL] = []
    elif q_type == "choice":
        if not isinstance(criteria, dict) or len(criteria) < 2:
            raise ValueError(
                "choice question requires criteria map with at least 2 options"
            )
        sorted_entries = sorted(
            ((str(k), "" if v is None else str(v)) for k, v in criteria.items()),
            key=lambda item: item[0].encode("utf-8"),
        )
        choice_wire = JspbArray([[[k, v] for k, v in sorted_entries]])
        inc_none = question.get(
            "includeNoneOfAbove", question.get("include_none_of_above")
        )
        if isinstance(inc_none, bool):
            choice_wire[1] = inc_none
        q_wire[DecisionQuestionIndex.CHOICE] = choice_wire.compact()
    elif q_type == "score":
        if not isinstance(criteria, list) or len(criteria) < 2:
            raise ValueError(
                "score question requires criteria list with at least 2 levels"
            )
        levels = [str(item) for item in criteria]
        q_wire[DecisionQuestionIndex.SCORE] = [levels]
    else:
        raise ValueError(f"Unsupported Decisions question type: {q_type!r}")

    return q_wire.compact()


def encode_decision_request_body(
    *,
    model: str = "models/gemini-decision-1",
    raw_input: str | dict[str, object] | Sequence[object],
    questions: dict[str, dict[str, object]],
    temperature: float | None = None,
    snapshot: str | None = None,
) -> str:
    if not questions:
        raise ValueError("questions must not be empty")
    norm_model = model if model.startswith("models/") else f"models/{model}"
    blocks = _normalize_interactions_blocks(raw_input)
    input_wire = [_encode_interactions_block(block) for block in blocks]
    questions_wire = [
        [str(q_key), _encode_decision_question(q_val)]
        for q_key, q_val in questions.items()
    ]
    body = JspbArray()
    body[CreateDecisionIndex.MODEL] = norm_model
    body[CreateDecisionIndex.INPUT] = input_wire
    body[CreateDecisionIndex.QUESTIONS] = questions_wire
    if temperature is not None and float(temperature) > 0:
        body[CreateDecisionIndex.TEMPERATURE] = float(temperature)
    if snapshot:
        body[CreateDecisionIndex.REQUEST_SNAPSHOT] = snapshot
    return json.dumps(body.compact(), separators=(",", ":"), ensure_ascii=False)


def encode_count_tokens_body(
    *,
    model: str,
    contents: list[AistudioContent],
) -> str:
    norm_model = model if model.startswith("models/") else f"models/{model}"
    payload = [norm_model, [content.to_wire() for content in contents]]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
