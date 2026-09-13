"""Application services for chat/image orchestration."""

from __future__ import annotations

import base64
import os
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.api.schemas import GeminiGenerateContentRequest, GeminiTool


from aistudio_api.infrastructure.gateway.model_defaults import resolve_model_defaults
from aistudio_api.infrastructure.gateway.wire_codec import build_tools_from_names
from aistudio_api.infrastructure.gateway.wire_types import (
    AistudioContent,
    AistudioImageOutputMode,
    AistudioPart,
    AistudioThinkingConfig,
    ThinkingLevel,
)
from dataclasses import dataclass


@dataclass
class NormalizedGeminiRequest:
    model: str
    contents: list[AistudioContent]
    system_instruction: AistudioContent | None
    tools: list[list[object]] | None
    safety_settings: list[list[object]] | None
    capture_prompt: str
    capture_images: list[str] | None
    cleanup_paths: list[str]
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    generation_config_overrides: dict[str, object] | None = None

    def __getitem__(self, key: str) -> object:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: object = None) -> object:
        return getattr(self, key, default)

SCHEMA_TYPE_CODES = {
    "string": 1,
    "number": 2,
    "integer": 3,
    "boolean": 4,
    "array": 5,
    "object": 6,
}


def cleanup_files(paths: list[str]):
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def inline_data_to_file(mime_type: str, data: str, tmp_dir: str = "/tmp") -> str:
    ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
    path = os.path.join(tmp_dir, f"aistudio_img_{uuid.uuid4().hex[:8]}.{ext}")
    with open(path, "wb") as file:
        file.write(base64.b64decode(data))
    return path


def encode_schema_to_wire(schema: dict[str, object], *, include_required: bool = True) -> list[object]:
    schema_type = str(schema.get("type") or "")
    type_code = SCHEMA_TYPE_CODES.get(schema_type, 0)
    wire: list[object] = [type_code]

    if schema_type == "array" and isinstance(schema.get("items"), dict):
        while len(wire) <= 5:
            wire.append(None)
        wire[5] = encode_schema_to_wire(
            schema["items"], include_required=include_required  # type: ignore[arg-type]
        )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        while len(wire) <= 6:
            wire.append(None)
        wire[6] = [
            [name, encode_schema_to_wire(prop, include_required=include_required)]  # type: ignore[arg-type]
            for name, prop in properties.items()
            if isinstance(prop, dict)
        ]

    required = schema.get("required")
    if include_required and isinstance(required, list):
        while len(wire) <= 7:
            wire.append(None)
        wire[7] = list(required)

    property_ordering = schema.get("propertyOrdering")
    if isinstance(property_ordering, list):
        while len(wire) <= 22:
            wire.append(None)
        wire[22] = list(property_ordering)

    return wire


def encode_function_declaration_to_wire(declaration: dict[str, object]) -> list[object]:
    if not declaration.get("name"):
        raise ValueError("functionDeclarations[].name is required")

    wire = [declaration["name"]]
    if declaration.get("description") is not None:
        while len(wire) <= 1:
            wire.append(None)
        wire[1] = declaration["description"]

    parameters = declaration.get("parameters")
    if isinstance(parameters, dict):
        while len(wire) <= 2:
            wire.append(None)
        wire[2] = encode_schema_to_wire(parameters, include_required=False)

    return wire


def _normalize_gemini_modalities(value: object) -> AistudioImageOutputMode | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("generationConfig.responseModalities must be a list")

    modalities = {str(item).strip().upper() for item in value if str(item).strip()}
    if not modalities:
        return None
    unknown = modalities - {"TEXT", "IMAGE"}
    if unknown:
        raise ValueError(
            f"Unsupported response modalities: {', '.join(sorted(unknown))}"
        )
    if "IMAGE" not in modalities:
        return None
    if "TEXT" in modalities:
        return AistudioImageOutputMode.text_and_image()
    return AistudioImageOutputMode.image_only()


def _normalize_gemini_thinking_config(value: object) -> list[object] | dict[str, object] | None:
    if value is None or isinstance(value, (list, dict)):
        if value is None or isinstance(value, list):
            return value
    if not isinstance(value, dict):
        raise ValueError(
            "generationConfig.thinkingConfig must be an object or wire array"
        )

    raw_level = value.get("thinkingLevel", value.get("level", ThinkingLevel.HIGH))
    raw_mode = value.get("mode", 1)
    if isinstance(raw_level, ThinkingLevel):
        level = raw_level
    elif isinstance(raw_level, int):
        level = ThinkingLevel(raw_level)
    else:
        level = ThinkingLevel[str(raw_level).strip().upper()]
    return AistudioThinkingConfig(level=level, mode=int(raw_mode)).to_wire()


def _normalize_gemini_image_config(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("generationConfig.imageConfig must be an object")

    aspect_ratio = value.get("aspectRatio")
    image_size = value.get("imageSize")
    if isinstance(aspect_ratio, str) and not aspect_ratio.strip():
        aspect_ratio = None
    if isinstance(image_size, str):
        image_size = image_size.strip() or None
    person_generation = value.get("personGeneration")
    if person_generation not in (None, ""):
        raise ValueError(
            "generationConfig.imageConfig.personGeneration is not supported yet"
        )

    normalized: dict[str, object] = {}
    if aspect_ratio is not None or image_size is not None:
        normalized["output_resolution"] = [aspect_ratio, image_size]
    return normalized


def _extract_google_search_tool_names(tool: GeminiTool, *, is_image_model: bool) -> list[str]:
    if tool.googleSearchRetrieval is not None:
        return ["google_search"]

    config = tool.googleSearch
    if config is None:
        return []
    if not is_image_model or not isinstance(config, dict):
        return ["google_search"]

    search_types = config.get("searchTypes")
    if not isinstance(search_types, dict):
        return ["google_search"]

    web_enabled = search_types.get("webSearch") is not None
    image_enabled = search_types.get("imageSearch") is not None
    if web_enabled and image_enabled:
        return ["google_search_and_image_search"]
    if image_enabled:
        return ["image_search"]
    return ["google_search"]


def _filter_default_tools_for_model(
    tool_names: tuple[str, ...], *, is_image_model: bool
) -> list[str]:
    names = [str(name).strip() for name in tool_names if str(name).strip()]
    if not is_image_model:
        return names
    allowed = {"google_search", "image_search", "google_search_and_image_search"}
    return [name for name in names if name in allowed]


# 内置搜索工具的覆盖关系：复合工具覆盖其组成部分，去重时据此跳过被覆盖的窄工具。
_BUILTIN_TOOL_COVERS = {
    "google_search_and_image_search": ("google_search", "image_search"),
}


def _drop_covered_builtin_tools(names: list[str], seen: set[str]) -> list[str]:
    """从 names 去掉已被 ``seen`` 覆盖或被同批 names 覆盖的内置工具。

    例如 seen 含 google_search_and_image_search 时，google_search 被视为冗余跳过；
    同一批 names 里两者并存时也只保留复合工具。
    """
    have: set[str] = set(seen)
    result: list[str] = []
    for name in names:
        if name in have:
            continue
        if any(
            name in subs and sup in have for sup, subs in _BUILTIN_TOOL_COVERS.items()
        ):
            continue
        result.append(name)
        have.add(name)
    return result


_GEMINI_SAFETY_CATEGORY_MAP = {
    "HARM_CATEGORY_HARASSMENT": 7,
    "HARM_CATEGORY_HATE_SPEECH": 8,
    "HARM_CATEGORY_SEXUALLY_EXPLICIT": 9,
    "HARM_CATEGORY_DANGEROUS_CONTENT": 10,
}

_GEMINI_SAFETY_THRESHOLD_MAP = {
    "BLOCK_LOW_AND_ABOVE": 1,
    "BLOCK_MEDIUM_AND_ABOVE": 2,
    "BLOCK_ONLY_HIGH": 3,
    "BLOCK_NONE": 4,
    "OFF": 5,
}


def _normalize_gemini_safety_settings(value: object) -> list[list[object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("safetySettings must be a list")

    normalized: list[list[object]] = []
    for item in value:
        if not hasattr(item, "category") or not hasattr(item, "threshold"):
            raise ValueError(f"Unsupported safety setting entry: {item!r}")

        category = _GEMINI_SAFETY_CATEGORY_MAP.get(str(item.category).strip().upper())
        if category is None:
            raise ValueError(f"Unsupported safety category: {item.category}")
        threshold = _GEMINI_SAFETY_THRESHOLD_MAP.get(
            str(item.threshold).strip().upper()
        )
        if threshold is None:
            raise ValueError(f"Unsupported safety threshold: {item.threshold}")
        normalized.append([None, None, category, threshold])
    return normalized


def normalize_gemini_request(
    req: GeminiGenerateContentRequest, requested_model: str, tmp_dir: str = "/tmp"
) -> NormalizedGeminiRequest:
    if not req.contents:
        raise ValueError("contents is required")

    model = (
        requested_model
        if requested_model.startswith("models/")
        else f"models/{requested_model}"
    )
    contents: list[AistudioContent] = []
    cleanup_paths: list[str] = []
    capture_prompt = "你好"
    capture_images: list[str] = []

    for content in req.contents:
        role = content.role or "user"
        parts: list[AistudioPart] = []
        text_parts: list[str] = []
        content_images: list[str] = []

        # 预先统计文本 Part 的位置索引，用于推断多 Part model 消息中的思考内容。
        # 约定：model 角色有 2 个及以上纯文本 Part 时，最后一个是正式回答，
        # 其余全是思考内容——即使客户端没有传 thought=true 字段。
        text_part_positions = [
            i for i, p in enumerate(content.parts) if p.text is not None
        ]
        infer_thinking = role == "model" and len(text_part_positions) >= 2

        for idx, part in enumerate(content.parts):
            if part.text is not None:
                # 显式 thought 字段优先；否则对 model 多文本 Part 按位置推断
                is_thought = bool(part.thought) or (
                    infer_thinking and idx != text_part_positions[-1]
                )
                parts.append(
                    AistudioPart(
                        text=part.text,
                        thought=is_thought,
                    )
                )
                text_parts.append(part.text)
                continue
            if part.inlineData is not None:
                parts.append(
                    AistudioPart(
                        inline_data=(part.inlineData.mimeType, part.inlineData.data),
                        thought_signature=part.thoughtSignature,
                    )
                )
                image_path = inline_data_to_file(
                    part.inlineData.mimeType, part.inlineData.data, tmp_dir=tmp_dir
                )
                content_images.append(image_path)
                cleanup_paths.append(image_path)
                continue
            if part.fileData is not None:
                raise ValueError("fileData is not supported yet")

        contents.append(AistudioContent(role=role, parts=parts))

        if role == "user":
            if text_parts:
                capture_prompt = "\n".join(text_parts)
            if content_images:
                capture_images = content_images

    system_instruction = None
    if req.systemInstruction is not None:
        system_instruction = AistudioContent(
            role=req.systemInstruction.role or "user",
            parts=[
                AistudioPart(text=part.text)
                if part.text is not None
                else AistudioPart(
                    inline_data=(part.inlineData.mimeType, part.inlineData.data) if part.inlineData else ("", ""),
                    thought_signature=part.thoughtSignature,
                )
                for part in req.systemInstruction.parts
                if part.text is not None or part.inlineData is not None
            ],
        )

    model_defaults = resolve_model_defaults(model)
    tools = None
    seen_builtin: set[str] = set()
    if req.tools is not None:
        tools = []
        for tool in req.tools:
            builtin_tool_names: list[str] = []
            if tool.codeExecution is not None:
                builtin_tool_names.append("code_execution")
            if tool.functionDeclarations:
                tools.append(
                    [
                        None,
                        [
                            encode_function_declaration_to_wire(decl)
                            for decl in tool.functionDeclarations
                        ],
                    ]
                )
            builtin_tool_names.extend(
                _extract_google_search_tool_names(
                    tool, is_image_model=model_defaults.is_image_model
                )
            )
            if tool.googleMaps is not None:
                builtin_tool_names.append("google_maps")
            if tool.urlContext is not None:
                builtin_tool_names.append("url_context")
            if builtin_tool_names:
                tools.extend(
                    build_tools_from_names(
                        builtin_tool_names,
                        model=model,
                        is_image_model=model_defaults.is_image_model,
                    )
                )
                seen_builtin.update(builtin_tool_names)

    # 注入 config.yaml 的 default_tools（内置工具，如 google_search）。
    #   req.tools is None → 客户端没传 tools，注入（原行为）
    #   req.tools 非空    → 客户端带了自定义工具，也合并 default_tools
    #                       （之前被跳过，导致模型想用内置工具时不可用）
    #   req.tools == []   → 客户端明确禁用所有工具，跳过（保留"空数组=禁用"语义）
    if model_defaults.default_tools and not (
        req.tools is not None and len(req.tools) == 0
    ):
        default_tool_names = _filter_default_tools_for_model(
            model_defaults.default_tools,
            is_image_model=model_defaults.is_image_model,
        )
        # 去重：跳过请求已显式声明的内置工具，并按覆盖关系去掉被复合工具覆盖的窄工具
        default_tool_names = _drop_covered_builtin_tools(
            default_tool_names, seen_builtin
        )
        injected = (
            build_tools_from_names(
                default_tool_names,
                model=model,
                is_image_model=model_defaults.is_image_model,
            )
            if default_tool_names
            else []
        )
        if tools is None:
            tools = injected
        else:
            tools.extend(injected)

    generation_config = req.generationConfig
    generation_config_overrides = {
        key: value
        for key, value in model_defaults.generation_config_overrides().items()
        if value is not None
    } or None
    if generation_config is not None:
        if generation_config_overrides is None:
            generation_config_overrides = {}
        if generation_config.stopSequences is not None:
            generation_config_overrides["stop_sequences"] = (
                generation_config.stopSequences
            )
        if generation_config.maxOutputTokens is not None:
            generation_config_overrides["max_tokens"] = (
                generation_config.maxOutputTokens
            )
        if generation_config.temperature is not None:
            generation_config_overrides["temperature"] = generation_config.temperature
        if generation_config.topP is not None:
            generation_config_overrides["top_p"] = generation_config.topP
        if generation_config.topK is not None:
            generation_config_overrides["top_k"] = generation_config.topK
        if generation_config.responseMimeType is not None:
            generation_config_overrides["response_mime_type"] = (
                generation_config.responseMimeType
            )
        if generation_config.responseSchema is not None:
            generation_config_overrides["response_schema"] = (
                encode_schema_to_wire(generation_config.responseSchema)
                if isinstance(generation_config.responseSchema, dict)
                else generation_config.responseSchema
            )
        if generation_config.presencePenalty is not None:
            generation_config_overrides["presence_penalty"] = (
                generation_config.presencePenalty
            )
        if generation_config.frequencyPenalty is not None:
            generation_config_overrides["frequency_penalty"] = (
                generation_config.frequencyPenalty
            )
        if generation_config.responseLogprobs is not None:
            generation_config_overrides["response_logprobs"] = (
                generation_config.responseLogprobs
            )
        if generation_config.logprobs is not None:
            generation_config_overrides["logprobs"] = generation_config.logprobs
        if generation_config.mediaResolution is not None:
            generation_config_overrides["media_resolution"] = (
                generation_config.mediaResolution
            )
        if generation_config.thinkingConfig is not None:
            generation_config_overrides["thinking_config"] = (
                _normalize_gemini_thinking_config(generation_config.thinkingConfig)
            )
        if generation_config.responseModalities is not None:
            image_output_mode = _normalize_gemini_modalities(
                generation_config.responseModalities
            )
            if image_output_mode is not None:
                generation_config_overrides["image_output_mode"] = image_output_mode
        if generation_config.imageConfig is not None:
            generation_config_overrides.update(
                _normalize_gemini_image_config(generation_config.imageConfig)
            )

    return NormalizedGeminiRequest(
        model=model,
        contents=contents,
        system_instruction=system_instruction,
        tools=tools if tools is not None else None,
        safety_settings=_normalize_gemini_safety_settings(req.safetySettings)
        if req.safetySettings is not None
        else None,
        capture_prompt=capture_prompt,
        capture_images=capture_images or None,
        cleanup_paths=cleanup_paths,
        temperature=generation_config.temperature if generation_config else None,
        top_p=generation_config.topP if generation_config else None,
        top_k=generation_config.topK if generation_config else None,
        max_tokens=generation_config.maxOutputTokens if generation_config else None,
        generation_config_overrides=generation_config_overrides or None,
    )
