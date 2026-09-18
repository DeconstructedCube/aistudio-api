"""Typed domain response models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GeneratedImage:
    mime: str
    data: bytes
    size: int
    thought_signature: str = ""


@dataclass
class Candidate:
    text: str = ""
    thinking: str = ""
    images: list[GeneratedImage] = field(default_factory=list)
    reasoning_images: list[GeneratedImage] = field(default_factory=list)
    function_calls: list[dict[str, object]] = field(default_factory=list)
    function_responses: list[dict[str, object]] = field(default_factory=list)
    thought_signature: str = ""
    sources: list[dict[str, object]] = field(default_factory=list)
    code_output: str = ""
    finish_reason: int | None = None
    finish_message: str = ""
    safety_ratings: list[dict[str, object]] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(
            self.text
            or self.images
            or self.code_output
            or self.function_calls
            or self.function_responses
        )


@dataclass
class ModelOutput:
    candidates: list[Candidate] = field(default_factory=list)
    model: str = ""
    raw_response: str = ""
    response_id: str = ""
    usage: dict[str, object] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.candidates[0].text if self.candidates else ""

    @property
    def thinking(self) -> str:
        return self.candidates[0].thinking if self.candidates else ""

    @property
    def images(self) -> list[GeneratedImage]:
        return self.candidates[0].images if self.candidates else []

    @property
    def reasoning_images(self) -> list[GeneratedImage]:
        return self.candidates[0].reasoning_images if self.candidates else []

    @property
    def function_calls(self) -> list[dict[str, object]]:
        return self.candidates[0].function_calls if self.candidates else []

    @property
    def function_responses(self) -> list[dict[str, object]]:
        return self.candidates[0].function_responses if self.candidates else []

    @property
    def sources(self) -> list[dict[str, object]]:
        return self.candidates[0].sources if self.candidates else []

    @property
    def code_output(self) -> str:
        return self.candidates[0].code_output if self.candidates else ""

    @property
    def has_content(self) -> bool:
        return any(c.has_content for c in self.candidates)


__all__ = [
    "Candidate",
    "GeneratedImage",
    "ModelOutput",
]
