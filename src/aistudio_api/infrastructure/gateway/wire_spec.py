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
    CACHED_CONTENT = 11
    LOCATION = 13


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
    THINKING_CONFIG = 16
    MEDIA_RESOLUTION = 17
    OUTPUT_RESOLUTION = 26


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
