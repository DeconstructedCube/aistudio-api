"""Typed representations for the reverse-engineered AI Studio wire body."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from .wire_spec import GenerationConfigIndex, JspbArray, PartIndex


class ThinkingLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    MINIMAL = 4


class MediaResolution(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class ImageOutputType(IntEnum):
    IMAGE = 2


@dataclass(frozen=True)
class AistudioThinkingConfig:
    level: ThinkingLevel = ThinkingLevel.HIGH
    mode: int = 1

    def to_wire(self) -> list:
        return [self.mode, None, None, int(self.level)]

    @classmethod
    def default(cls) -> AistudioThinkingConfig:
        return cls()


@dataclass(frozen=True)
class AistudioImageOutputMode:
    output_type: ImageOutputType = ImageOutputType.IMAGE
    include_text: bool = False

    def to_wire(self) -> list[int]:
        if self.include_text:
            return [int(self.output_type), 1]
        return [int(self.output_type)]

    @classmethod
    def image_only(cls) -> AistudioImageOutputMode:
        return cls(include_text=False)

    @classmethod
    def text_and_image(cls) -> AistudioImageOutputMode:
        return cls(include_text=True)


@dataclass
class AistudioGenerationConfig:
    values: list = field(default_factory=list)

    @property
    def stop_sequences(self):
        idx = GenerationConfigIndex.STOP_SEQUENCES
        return self.values[idx] if len(self.values) > idx else None

    @stop_sequences.setter
    def stop_sequences(self, value):
        idx = GenerationConfigIndex.STOP_SEQUENCES
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def max_tokens(self):
        idx = GenerationConfigIndex.MAX_TOKENS
        return self.values[idx] if len(self.values) > idx else None

    @max_tokens.setter
    def max_tokens(self, value):
        idx = GenerationConfigIndex.MAX_TOKENS
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def temperature(self):
        idx = GenerationConfigIndex.TEMPERATURE
        return self.values[idx] if len(self.values) > idx else None

    @temperature.setter
    def temperature(self, value):
        idx = GenerationConfigIndex.TEMPERATURE
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def top_p(self):
        idx = GenerationConfigIndex.TOP_P
        return self.values[idx] if len(self.values) > idx else None

    @top_p.setter
    def top_p(self, value):
        idx = GenerationConfigIndex.TOP_P
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def top_k(self):
        idx = GenerationConfigIndex.TOP_K
        return self.values[idx] if len(self.values) > idx else None

    @top_k.setter
    def top_k(self, value):
        idx = GenerationConfigIndex.TOP_K
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def response_mime_type(self):
        idx = GenerationConfigIndex.RESPONSE_MIME_TYPE
        return self.values[idx] if len(self.values) > idx else None

    @response_mime_type.setter
    def response_mime_type(self, value):
        idx = GenerationConfigIndex.RESPONSE_MIME_TYPE
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def response_schema(self):
        idx = GenerationConfigIndex.RESPONSE_SCHEMA
        return self.values[idx] if len(self.values) > idx else None

    @response_schema.setter
    def response_schema(self, value):
        idx = GenerationConfigIndex.RESPONSE_SCHEMA
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def presence_penalty(self):
        idx = GenerationConfigIndex.PRESENCE_PENALTY
        return self.values[idx] if len(self.values) > idx else None

    @presence_penalty.setter
    def presence_penalty(self, value):
        idx = GenerationConfigIndex.PRESENCE_PENALTY
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def frequency_penalty(self):
        idx = GenerationConfigIndex.FREQUENCY_PENALTY
        return self.values[idx] if len(self.values) > idx else None

    @frequency_penalty.setter
    def frequency_penalty(self, value):
        idx = GenerationConfigIndex.FREQUENCY_PENALTY
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def response_logprobs(self):
        idx = GenerationConfigIndex.RESPONSE_LOGPROBS
        return self.values[idx] if len(self.values) > idx else None

    @response_logprobs.setter
    def response_logprobs(self, value):
        idx = GenerationConfigIndex.RESPONSE_LOGPROBS
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def logprobs(self):
        idx = GenerationConfigIndex.LOGPROBS
        return self.values[idx] if len(self.values) > idx else None

    @logprobs.setter
    def logprobs(self, value):
        idx = GenerationConfigIndex.LOGPROBS
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def image_output_mode(self):
        idx = GenerationConfigIndex.IMAGE_OUTPUT_MODE
        return self.values[idx] if len(self.values) > idx else None

    @image_output_mode.setter
    def image_output_mode(self, value):
        idx = GenerationConfigIndex.IMAGE_OUTPUT_MODE
        self._ensure_len(idx + 1)
        if isinstance(value, AistudioImageOutputMode):
            value = value.to_wire()
        self.values[idx] = value

    @property
    def thinking_config(self):
        idx = GenerationConfigIndex.THINKING_CONFIG
        return self.values[idx] if len(self.values) > idx else None

    @thinking_config.setter
    def thinking_config(self, value):
        idx = GenerationConfigIndex.THINKING_CONFIG
        self._ensure_len(idx + 1)
        self.values[idx] = value

    @property
    def media_resolution(self):
        idx = GenerationConfigIndex.MEDIA_RESOLUTION
        return self.values[idx] if len(self.values) > idx else None

    @media_resolution.setter
    def media_resolution(self, value):
        idx = GenerationConfigIndex.MEDIA_RESOLUTION
        self._ensure_len(idx + 1)
        if isinstance(value, MediaResolution):
            value = int(value)
        self.values[idx] = value

    @property
    def output_resolution(self):
        idx = GenerationConfigIndex.OUTPUT_RESOLUTION
        return self.values[idx] if len(self.values) > idx else None

    @output_resolution.setter
    def output_resolution(self, value):
        idx = GenerationConfigIndex.OUTPUT_RESOLUTION
        self._ensure_len(idx + 1)
        self.values[idx] = value

    def clear_gemma_thinking_budget(self):
        idx = GenerationConfigIndex.THINKING_CONFIG
        if len(self.values) > idx:
            self.values[idx] = None
    def enable_default_thinking(self):
        if self.thinking_config is None:
            self.thinking_config = AistudioThinkingConfig.default().to_wire()

    def sanitize_for_plain_text(self):
        self.response_mime_type = "text/plain"
        self.response_schema = None
        self.thinking_config = None

    def _ensure_len(self, size: int):
        while len(self.values) < size:
            self.values.append(None)


@dataclass
class AistudioPart:
    text: str | None = None
    inline_data: tuple[str, str] | None = None
    file_id: str | None = None
    function_call: tuple[str, object] | tuple[str, object, str] | None = None
    function_response: tuple[str, object] | tuple[str, object, str] | None = None
    thought_signature: str | None = None
    thought: bool = False

    def to_wire(self) -> list[object]:
        wire = JspbArray()
        if self.file_id:
            wire[PartIndex.FILE_DATA] = [self.file_id]
            return wire.compact()
        if self.inline_data:
            mime, b64 = self.inline_data
            wire[PartIndex.INLINE_DATA] = [mime, b64]
            if self.thought_signature:
                wire[PartIndex.THOUGHT_SIGNATURE] = self.thought_signature
            return wire.compact()
        if self.function_call:
            name, args = self.function_call[0], self.function_call[1]
            call_id = self.function_call[2] if len(self.function_call) > 2 else None
            function_call = [name, _encode_wire_args(args)]
            if call_id:
                function_call.append(call_id)
            wire[PartIndex.FUNCTION_CALL_ALT] = function_call
            if self.thought_signature:
                wire[PartIndex.THOUGHT_SIGNATURE] = self.thought_signature
            return wire.compact()
        if self.function_response:
            name, response = self.function_response[0], self.function_response[1]
            call_id = (
                self.function_response[2] if len(self.function_response) > 2 else None
            )
            function_response = [name, _encode_wire_args(response)]
            if call_id:
                function_response.append(call_id)
            wire[PartIndex.FUNCTION_RESPONSE_ALT] = function_response
            return wire.compact()
        # Text part — mark as thinking when thought=True (wire index 12 = 1).
        if self.thought:
            wire[PartIndex.TEXT] = self.text
            wire[PartIndex.THOUGHT_ALT_FLAG] = 1
            if self.thought_signature:
                wire[PartIndex.THOUGHT_SIGNATURE] = self.thought_signature
            return wire.compact()
        if self.thought_signature:
            wire[PartIndex.TEXT] = self.text
            wire[PartIndex.THOUGHT_SIGNATURE] = self.thought_signature
            return wire.compact()
        wire[PartIndex.TEXT] = self.text
        return wire.compact()


def _encode_wire_args(value):
    if isinstance(value, dict):
        return [_encode_wire_struct_fields(value)]
    return value


def _encode_wire_struct_fields(d: dict):
    return [[key, _encode_wire_value(val)] for key, val in d.items()]


def _encode_wire_value(value):
    if value is None:
        return [0]
    if isinstance(value, bool):
        return [None, None, None, value]
    if isinstance(value, (int, float)):
        return [None, value]
    if isinstance(value, str):
        return [None, None, value]
    if isinstance(value, dict):
        return [None, None, None, None, [_encode_wire_struct_fields(value)]]
    if isinstance(value, list):
        return [
            None,
            None,
            None,
            None,
            None,
            [[_encode_wire_value(item) for item in value]],
        ]
    return [None, None, str(value)]


@dataclass
class AistudioContent:
    role: str
    parts: list[AistudioPart]

    def to_wire(self):
        return [[part.to_wire() for part in self.parts], self.role]


@dataclass
class AistudioRequest:
    model: str
    contents: list[AistudioContent]
    safety_settings: list | None
    generation_config: AistudioGenerationConfig
    snapshot: str | None
    system_instruction: AistudioContent | None
    tools: list[list] | None
    evergreen_model_uri: str | None = None
    tool_config: list | None = None
    request_flag: int | None = None
    cached_content: str | None = None
    location: list | None = None
    raw_body: list = field(default_factory=list)
