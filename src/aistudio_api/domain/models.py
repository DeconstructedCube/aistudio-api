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
class CandidatePart:
    kind: str
    text: str = ""
    has_text: bool = False
    thought: bool = False
    thought_signature: str = ""
    image: GeneratedImage | None = None
    payload: dict[str, object] | None = None
    chunk_start: bool = False
    end_of_chunk: bool = True


class ThoughtSignatureEvent(str):
    """Streaming thought_signature event string annotated with carrier affinity."""

    standalone: bool
    thought: bool
    empty_text_carrier: bool
    end_of_chunk: bool

    def __new__(
        cls,
        value: str,
        *,
        standalone: bool = False,
        thought: bool = False,
        empty_text_carrier: bool = False,
        end_of_chunk: bool = True,
    ) -> ThoughtSignatureEvent:
        obj = super().__new__(cls, value)
        obj.standalone = standalone
        obj.thought = thought
        obj.empty_text_carrier = empty_text_carrier
        obj.end_of_chunk = end_of_chunk
        return obj


@dataclass
class Candidate:
    text: str = ""
    thinking: str = ""
    images: list[GeneratedImage] = field(default_factory=list)
    reasoning_images: list[GeneratedImage] = field(default_factory=list)
    function_calls: list[dict[str, object]] = field(default_factory=list)
    function_responses: list[dict[str, object]] = field(default_factory=list)
    thought_signature: str = ""
    thinking_signature: str = ""
    text_signature: str = ""
    standalone_signature: str = ""
    standalone_signatures: list[str] = field(default_factory=list)
    parts: list[CandidatePart] = field(default_factory=list)
    sources: list[dict[str, object]] = field(default_factory=list)
    code_output: str = ""
    finish_reason: int | None = None
    finish_message: str = ""
    safety_ratings: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.parts:
            return
        built: list[CandidatePart] = []
        if self.thinking:
            built.append(
                CandidatePart(
                    kind="thinking",
                    text=self.thinking,
                    has_text=True,
                    thought=True,
                    thought_signature=self.thinking_signature,
                )
            )
        for img in self.reasoning_images:
            built.append(
                CandidatePart(
                    kind="reasoning_image",
                    thought=True,
                    thought_signature=img.thought_signature,
                    image=img,
                )
            )
        effective_text_sig = self.text_signature or (
            self.standalone_signature
            if (self.text and not self.function_calls and not self.images)
            else ""
        )
        if self.text:
            built.append(
                CandidatePart(
                    kind="text",
                    text=self.text,
                    has_text=True,
                    thought_signature=effective_text_sig,
                )
            )
        for img in self.images:
            built.append(
                CandidatePart(
                    kind="image",
                    thought_signature=img.thought_signature,
                    image=img,
                )
            )
        for fc in self.function_calls:
            built.append(
                CandidatePart(
                    kind="function_call",
                    thought_signature=str(fc.get("thought_signature") or ""),
                    payload=fc,
                )
            )
        for fr in self.function_responses:
            built.append(CandidatePart(kind="function_response", payload=fr))
        extra_sigs = list(self.standalone_signatures)
        if self.standalone_signature and self.standalone_signature not in extra_sigs:
            extra_sigs.append(self.standalone_signature)
        for sig in extra_sigs:
            if sig and sig != effective_text_sig:
                built.append(
                    CandidatePart(
                        kind="standalone_signature",
                        thought_signature=sig,
                    )
                )
        self.parts = built

    @property
    def has_content(self) -> bool:
        return bool(
            self.text
            or self.images
            or self.code_output
            or self.function_calls
            or self.function_responses
            or any(
                part.kind
                in (
                    "text",
                    "image",
                    "function_call",
                    "function_response",
                    "executable_code",
                    "code_execution_result",
                    "file_data",
                    "video_metadata",
                )
                for part in self.parts
            )
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
    "CandidatePart",
    "GeneratedImage",
    "ModelOutput",
    "ThoughtSignatureEvent",
]
