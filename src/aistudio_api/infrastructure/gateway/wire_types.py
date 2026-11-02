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
    level: ThinkingLevel | None = None
    mode: int = 1
    budget: int | None = None

    def to_wire(self) -> list[object]:
        wire = JspbArray([self.mode])
        if self.budget is not None:
            wire[1] = int(self.budget)
        if self.level is not None:
            wire[3] = int(self.level)
        return wire.compact()


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
        self._set_slot(GenerationConfigIndex.STOP_SEQUENCES, value)

    @property
    def max_tokens(self):
        idx = GenerationConfigIndex.MAX_TOKENS
        return self.values[idx] if len(self.values) > idx else None

    @max_tokens.setter
    def max_tokens(self, value):
        self._set_slot(GenerationConfigIndex.MAX_TOKENS, value)

    @property
    def temperature(self):
        idx = GenerationConfigIndex.TEMPERATURE
        return self.values[idx] if len(self.values) > idx else None

    @temperature.setter
    def temperature(self, value):
        self._set_slot(GenerationConfigIndex.TEMPERATURE, value)

    @property
    def top_p(self):
        idx = GenerationConfigIndex.TOP_P
        return self.values[idx] if len(self.values) > idx else None

    @top_p.setter
    def top_p(self, value):
        self._set_slot(GenerationConfigIndex.TOP_P, value)

    @property
    def top_k(self):
        idx = GenerationConfigIndex.TOP_K
        return self.values[idx] if len(self.values) > idx else None

    @top_k.setter
    def top_k(self, value):
        self._set_slot(GenerationConfigIndex.TOP_K, value)

    @property
    def response_mime_type(self):
        idx = GenerationConfigIndex.RESPONSE_MIME_TYPE
        return self.values[idx] if len(self.values) > idx else None

    @response_mime_type.setter
    def response_mime_type(self, value):
        self._set_slot(GenerationConfigIndex.RESPONSE_MIME_TYPE, value)

    @property
    def response_schema(self):
        idx = GenerationConfigIndex.RESPONSE_SCHEMA
        return self.values[idx] if len(self.values) > idx else None

    @response_schema.setter
    def response_schema(self, value):
        self._set_slot(GenerationConfigIndex.RESPONSE_SCHEMA, value)

    @property
    def presence_penalty(self):
        idx = GenerationConfigIndex.PRESENCE_PENALTY
        return self.values[idx] if len(self.values) > idx else None

    @presence_penalty.setter
    def presence_penalty(self, value):
        self._set_slot(GenerationConfigIndex.PRESENCE_PENALTY, value)

    @property
    def frequency_penalty(self):
        idx = GenerationConfigIndex.FREQUENCY_PENALTY
        return self.values[idx] if len(self.values) > idx else None

    @frequency_penalty.setter
    def frequency_penalty(self, value):
        self._set_slot(GenerationConfigIndex.FREQUENCY_PENALTY, value)

    @property
    def response_logprobs(self):
        idx = GenerationConfigIndex.RESPONSE_LOGPROBS
        return self.values[idx] if len(self.values) > idx else None

    @response_logprobs.setter
    def response_logprobs(self, value):
        self._set_slot(GenerationConfigIndex.RESPONSE_LOGPROBS, value)

    @property
    def logprobs(self):
        idx = GenerationConfigIndex.LOGPROBS
        return self.values[idx] if len(self.values) > idx else None

    @logprobs.setter
    def logprobs(self, value):
        self._set_slot(GenerationConfigIndex.LOGPROBS, value)

    @property
    def image_output_mode(self):
        idx = GenerationConfigIndex.IMAGE_OUTPUT_MODE
        return self.values[idx] if len(self.values) > idx else None

    @image_output_mode.setter
    def image_output_mode(self, value):
        if isinstance(value, AistudioImageOutputMode):
            value = value.to_wire()
        self._set_slot(GenerationConfigIndex.IMAGE_OUTPUT_MODE, value)

    @property
    def thinking_config(self):
        idx = GenerationConfigIndex.THINKING_CONFIG
        return self.values[idx] if len(self.values) > idx else None

    @thinking_config.setter
    def thinking_config(self, value):
        self._set_slot(GenerationConfigIndex.THINKING_CONFIG, value)

    @property
    def media_resolution(self):
        idx = GenerationConfigIndex.MEDIA_RESOLUTION
        return self.values[idx] if len(self.values) > idx else None

    @media_resolution.setter
    def media_resolution(self, value):
        if isinstance(value, MediaResolution):
            value = int(value)
        self._set_slot(GenerationConfigIndex.MEDIA_RESOLUTION, value)

    @property
    def seed(self):
        idx = GenerationConfigIndex.SEED
        return self.values[idx] if len(self.values) > idx else None

    @seed.setter
    def seed(self, value):
        self._set_slot(GenerationConfigIndex.SEED, value)

    @property
    def output_resolution(self):
        idx = GenerationConfigIndex.OUTPUT_RESOLUTION
        return self.values[idx] if len(self.values) > idx else None

    @output_resolution.setter
    def output_resolution(self, value):
        self._set_slot(GenerationConfigIndex.OUTPUT_RESOLUTION, value)

    def _set_slot(self, idx: int, value: object) -> None:
        if value is None:
            if len(self.values) > idx:
                self.values[idx] = None
                while self.values and self.values[-1] is None:
                    self.values.pop()
            return
        self._ensure_len(idx + 1)
        self.values[idx] = value

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
    executable_code: tuple[str, str] | None = None
    code_execution_result: tuple[str, str] | None = None
    thought_signature: str | None = None
    thought: bool = False

    def to_wire(self) -> list[object]:
        wire = JspbArray()
        if self.thought:
            wire[PartIndex.THOUGHT_ALT_FLAG] = 1
        if self.thought_signature:
            wire[PartIndex.THOUGHT_SIGNATURE] = self.thought_signature
        if self.file_id:
            wire[PartIndex.FILE_DATA] = [self.file_id]
            return wire.compact()
        if self.inline_data:
            mime, b64 = self.inline_data
            wire[PartIndex.INLINE_DATA] = [mime, b64]
            return wire.compact()
        if self.function_call:
            name, args = self.function_call[0], self.function_call[1]
            call_id = self.function_call[2] if len(self.function_call) > 2 else None
            function_call = [name, _encode_wire_args(args)]
            if call_id:
                function_call.append(call_id)
            wire[PartIndex.FUNCTION_CALL_ALT] = function_call
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
        if self.executable_code:
            lang, code = self.executable_code
            lang_code = 1 if str(lang).upper() in ("1", "PYTHON") else 0
            wire[PartIndex.EXECUTABLE_CODE] = [lang_code, code]
            return wire.compact()
        if self.code_execution_result:
            outcome, output = self.code_execution_result
            outcome_map = {
                "OUTCOME_OK": 1,
                "OUTCOME_FAILED": 2,
                "OUTCOME_DEADLINE_EXCEEDED": 3,
            }
            outcome_code = (
                outcome_map.get(str(outcome).upper(), 1)
                if isinstance(outcome, str)
                else int(outcome)
            )
            wire[PartIndex.CODE_RESULT] = [outcome_code, output]
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

    @classmethod
    def from_user_prompt(
        cls,
        prompt: str,
        images: list[tuple[str, str]] | None = None,
    ) -> AistudioContent:
        parts: list[AistudioPart] = [
            AistudioPart(inline_data=item) for item in (images or [])
        ]
        parts.append(AistudioPart(text=prompt))
        return cls(role="user", parts=parts)


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
    request_flag: int | None = None
    session_token: str | None = None
    location: list | None = None
    raw_body: list = field(default_factory=list)
