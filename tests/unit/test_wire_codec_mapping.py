from pathlib import Path

from aistudio_api.infrastructure.gateway.wire_codec import AistudioWireCodec
from aistudio_api.infrastructure.gateway.wire_types import (
    AistudioGenerationConfig,
    AistudioImageOutputMode,
    AistudioThinkingConfig,
    MediaResolution,
    ThinkingLevel,
)


ROOT = Path(__file__).resolve().parents[1]


def test_decode_image_request_maps_generation_config_fields_from_proto_indexes():
    codec = AistudioWireCodec()
    raw = (ROOT / "test-image-input.json").read_text()

    request = codec.decode(raw)

    assert request.model == "models/gemini-3.1-flash-image-preview"
    assert request.generation_config.stop_sequences == ["6"]
    assert request.generation_config.max_tokens == 65536
    assert request.generation_config.temperature == 1
    assert request.generation_config.top_p == 0.95
    assert request.generation_config.top_k == 64
    assert request.generation_config.image_output_mode == [2, 1]
    assert request.generation_config.thinking_config == [1, None, None, 3]
    assert request.request_flag == 1
    assert request.cached_content is not None and request.cached_content.startswith("v1_")


def test_encode_preserves_newly_mapped_proto_fields():
    codec = AistudioWireCodec()
    raw = (ROOT / "test-image-input.json").read_text()

    request = codec.decode(raw)
    encoded = codec.encode(request)
    reparsed = codec.decode(encoded)

    assert reparsed.generation_config.stop_sequences == ["6"]
    assert reparsed.generation_config.image_output_mode == [2, 1]
    assert reparsed.generation_config.thinking_config == [1, None, None, 3]
    assert reparsed.request_flag == 1
    assert reparsed.cached_content == request.cached_content


def test_thinking_config_encodes_high_level_wire_shape():
    assert AistudioThinkingConfig(ThinkingLevel.HIGH).to_wire() == [1, None, None, 3]


def test_generation_config_enables_default_thinking():
    config = AistudioGenerationConfig([])

    config.enable_default_thinking()

    assert config.thinking_config == [1, None, None, 3]
    assert config.media_resolution is None


def test_generation_config_accepts_readable_image_output_mode_wrapper():
    config = AistudioGenerationConfig([])

    config.image_output_mode = AistudioImageOutputMode.text_and_image()

    assert config.image_output_mode == [2, 1]


def test_generation_config_accepts_media_resolution_enum():
    config = AistudioGenerationConfig([])

    config.media_resolution = MediaResolution.MEDIUM

    assert config.media_resolution == 2
