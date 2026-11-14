"""Protobuf-over-JSON (JSPB) wire specification indexes and typed containers.

Centralizes all raw wire array offsets defined in Google AI Studio
gRPC-web/MakerSuiteService protocol specifications.
"""

from __future__ import annotations

from enum import IntEnum


class TopLevelIndex(IntEnum):
    """Indexes in the top-level GenerateContent request array."""

    MODEL = 0
    CONTENTS = 1
    SAFETY_SETTINGS = 2
    GENERATION_CONFIG = 3
    SNAPSHOT = 4
    SYSTEM_INSTRUCTION = 5
    TOOLS = 6
    EVERGREEN_MODEL_URI = 7
    REQUEST_FLAG = 10
    SESSION_TOKEN = 11
    REQUEST_CONTEXT = 13


class PartIndex(IntEnum):
    """Indexes in a Content Part array."""

    THOUGHT_LEAD = 0
    TEXT = 1
    INLINE_DATA = 2
    FUNCTION_CALL = 3
    FUNCTION_RESPONSE = 4
    FILE_DATA = 5
    EXECUTABLE_CODE = 8
    CODE_RESULT = 9
    THOUGHT_FLAG = 10
    FUNCTION_CALL_ALT = 10
    FUNCTION_RESPONSE_ALT = 11
    THOUGHT_ALT_FLAG = 12
    THOUGHT_SIGNATURE = 14


class GenerationConfigIndex(IntEnum):
    """Indexes in the generationConfig array."""

    STOP_SEQUENCES = 1
    MAX_TOKENS = 3
    TEMPERATURE = 4
    TOP_P = 5
    TOP_K = 6
    RESPONSE_MIME_TYPE = 7
    RESPONSE_SCHEMA = 8
    PRESENCE_PENALTY = 9
    FREQUENCY_PENALTY = 10
    RESPONSE_LOGPROBS = 11
    LOGPROBS = 12
    IMAGE_OUTPUT_MODE = 14
    RESPONSE_MODALITIES = 14
    SPEECH_CONFIG = 15
    THINKING_CONFIG = 16
    MEDIA_RESOLUTION = 17
    SEED = 18
    VIDEO_CONFIG = 24
    OUTPUT_RESOLUTION = 26
    ARGON_CAPABILITY_FLAG = 45


class SchemaIndex(IntEnum):
    """Indexes in a OpenAPI/JSPB Schema array (capped at index 22)."""

    TYPE = 0
    FORMAT = 1
    DESCRIPTION = 2
    NULLABLE = 3
    ENUM = 4
    ITEMS = 5
    PROPERTIES = 6
    REQUIRED = 7
    MIN_PROPERTIES = 8
    MAX_PROPERTIES = 9
    MINIMUM = 10
    MAXIMUM = 11
    MIN_LENGTH = 12
    MAX_LENGTH = 13
    PATTERN = 14
    EXAMPLE = 15
    ONE_OF = 16
    ANY_OF = 17
    ALL_OF = 18
    NOT = 19
    MAX_ITEMS = 20
    MIN_ITEMS = 21
    PROPERTY_ORDERING = 22


class JspbArray(list):
    """Self-padding, trailing-null-trimming list container for sparse JSPB arrays.

    Automatically pads intermediate slots with None upon indexed assignment,
    and supports compacting/trimming trailing nulls up to a maximum bound.
    """

    def __init__(self, initial: list[object] | None = None) -> None:
        if initial:
            super().__init__(initial)
        else:
            super().__init__()

    def __setitem__(self, index: int, value: object) -> None:  # type: ignore[override]
        while len(self) <= index:
            self.append(None)
        super().__setitem__(index, value)

    def get_at(self, index: int, default: object = None) -> object:
        """Safely get element at index or return default if out of bounds."""
        return self[index] if index < len(self) else default

    def compact(self, max_index: int | None = None) -> list[object]:
        """Return a plain list capped at max_index with trailing None values trimmed."""
        res = list(self)
        if max_index is not None and len(res) > max_index + 1:
            res = res[: max_index + 1]
        while res and res[-1] is None:
            res.pop()
        return res


WIRE_FINISH_REASON_CONTINUING = 22


class CreateDecisionIndex(IntEnum):
    """Indexes in the top-level MakerSuiteService/CreateDecision request array."""

    MODEL = 0
    INPUT = 1
    QUESTIONS = 2
    TEMPERATURE = 3
    REQUEST_SNAPSHOT = 4


class DecisionQuestionIndex(IntEnum):
    """Indexes in CreateDecisionRequest.Question."""

    INSTRUCTIONS = 0
    NOUL = 1
    CHOICE = 2
    SCORE = 3


class DecisionAnswerIndex(IntEnum):
    """Indexes in CreateDecisionResponse.Answer."""

    CONFIDENCE = 0
    STATUS = 1
    NOUL_DETAIL = 3
    CHOICE_DETAIL = 4
    SCORE_DETAIL = 5


class InteractionsContentIndex(IntEnum):
    """Indexes in google.internal.alkali.applications.makersuite.v1.interactions.Content."""

    TEXT = 0
    IMAGE = 1
    AUDIO = 2
    DOCUMENT = 3
    VIDEO = 4


INTERACTIONS_IMAGE_MIME_TYPES: dict[str, int] = {
    "image/png": 1,
    "image/jpeg": 2,
    "image/jpg": 2,
    "image/webp": 3,
    "image/heic": 4,
    "image/heif": 5,
    "image/gif": 7,
    "image/bmp": 8,
    "image/tiff": 9,
}

INTERACTIONS_AUDIO_MIME_TYPES: dict[str, int] = {
    "audio/wav": 1,
    "audio/mp3": 2,
    "audio/aiff": 3,
    "audio/aac": 4,
    "audio/ogg": 5,
    "audio/flac": 6,
    "audio/mpeg": 7,
    "audio/m4a": 8,
    "audio/l16": 9,
    "audio/s16le": 10,
    "audio/opus": 11,
    "audio/alaw": 12,
    "audio/mulaw": 13,
}

INTERACTIONS_VIDEO_MIME_TYPES: dict[str, int] = {
    "video/mp4": 1,
    "video/mpeg": 2,
    "video/mpg": 3,
    "video/mov": 4,
    "video/avi": 5,
    "video/x-flv": 6,
    "video/webm": 7,
    "video/wmv": 8,
    "video/3gpp": 9,
}

DECISION_USAGE_MODALITY_NAMES: dict[int, str] = {
    1: "text",
    2: "image",
    3: "audio",
    4: "video",
    5: "document",
}
