import base64
import json
import struct
from typing import Any

from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiGenerationConfig,
    GeminiPart,
    GeminiPrebuiltVoiceConfig,
    GeminiSpeechConfig,
    GeminiVoiceConfig,
)
from aistudio_api.application.chat_service import (
    _normalize_gemini_modalities,
    encode_speech_config_to_wire,
    normalize_gemini_request,
)
from aistudio_api.infrastructure.gateway.model_discovery import model_discovery
from aistudio_api.infrastructure.gateway.wire_codec import encode_request_body
from aistudio_api.infrastructure.gateway.wire_parser import (
    parse_response_chunk,
    pcm_to_wav,
)
from aistudio_api.infrastructure.gateway.wire_spec import GenerationConfigIndex


def test_normalize_modalities_audio_and_video():
    assert _normalize_gemini_modalities(["AUDIO"]) == [3]
    assert _normalize_gemini_modalities(["VIDEO"]) == [4]
    assert _normalize_gemini_modalities(["VIDEO", "TEXT"]) == [4, 1]


def test_encode_speech_config_single_voice():
    config = GeminiSpeechConfig(
        voiceConfig=GeminiVoiceConfig(
            prebuiltVoiceConfig=GeminiPrebuiltVoiceConfig(voiceName="Kore")
        )
    )
    wire = encode_speech_config_to_wire(config)
    assert wire == [[["Kore"]]]


def test_encode_speech_config_multi_speaker():
    config: dict[str, Any] = {
        "multiSpeakerVoiceConfig": {
            "mode": "CONVERSATIONAL",
            "speakerVoiceConfigs": [
                {
                    "speaker": "Joe",
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}},
                },
                {
                    "speaker": "Jane",
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}},
                },
            ],
        }
    }
    wire = encode_speech_config_to_wire(config)
    assert wire is not None and isinstance(wire[2], list)
    multi_wire: list[Any] = wire[2]
    assert multi_wire[1] == [["Joe", [["Kore"]]], ["Jane", [["Puck"]]]]
    assert multi_wire[2] == 2  # CONVERSATIONAL


def test_tts_model_auto_defaults():
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="Hello TTS")])],
        generationConfig=GeminiGenerationConfig(temperature=0.7),
    )
    normalized = normalize_gemini_request(req, "gemini-3.8-flash-tts")
    overrides = normalized.generation_config_overrides or {}
    assert overrides.get("response_modalities") == [3]
    assert overrides.get("speech_config") == [[["Zephyr"]]]
    assert overrides.get("thinking_config") is None
    assert overrides.get("max_tokens") is None


def test_pcm_to_wav_format():
    pcm_sample = b"\x00\x00" * 240  # 10ms of silence at 24kHz 16-bit
    wav = pcm_to_wav(pcm_sample, sample_rate=24000, channels=1, bit_depth=16)
    assert len(wav) == 44 + len(pcm_sample)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    audio_format, channels, rate, byte_rate, align, bits = struct.unpack(
        "<HHIIHH", wav[20:36]
    )
    assert audio_format == 1
    assert channels == 1
    assert rate == 24000
    assert byte_rate == 48000
    assert align == 2
    assert bits == 16
    assert wav[36:40] == b"data"


def test_wire_parser_audio_decoding_and_wav_encapsulation():
    pcm_data = b"\x12\x34" * 100
    b64_pcm = base64.b64encode(pcm_data).decode("ascii")

    # Correct Protobuf wire chunk structure:
    # chunk[0][0] is candidate: [[[parts], "model"], finish_reason, ...]
    part = [None, None, ["audio/l16;rate=24000", b64_pcm]]
    candidate_content: list[object] = [[part], "model"]
    raw_candidate: list[object] = [candidate_content]
    candidates_container: list[object] = [raw_candidate]
    chunk: list[object] = [candidates_container]

    candidate = parse_response_chunk(chunk)
    assert len(candidate.images) == 1
    media = candidate.images[0]
    assert media.mime == "audio/wav"
    assert media.data.startswith(b"RIFF")
    assert media.data[44:] == pcm_data


def test_wire_parser_video_decoding():
    video_bytes = b"\x00\x00\x00\x18ftypmp42"
    b64_video = base64.b64encode(video_bytes).decode("ascii")

    part = [None, None, ["video/mp4", b64_video]]
    candidate_content: list[object] = [[part], "model"]
    raw_candidate: list[object] = [candidate_content]
    candidates_container: list[object] = [raw_candidate]
    chunk: list[object] = [candidates_container]

    candidate = parse_response_chunk(chunk)
    assert len(candidate.images) == 1
    media = candidate.images[0]
    assert media.mime == "video/mp4"
    assert media.data == video_bytes


def test_wire_codec_slots_for_speech_and_modalities():
    wire_json = encode_request_body(
        model="gemini-3.8-flash-tts",
        prompt="Synthesize this",
        generation_config_overrides={
            "response_modalities": [3],
            "speech_config": [[["Kore"]]],
        },
    )
    wire = json.loads(wire_json)
    gc = wire[
        GenerationConfigIndex.STOP_SEQUENCES + 2
    ]  # top level generation config slot 3
    assert gc[GenerationConfigIndex.RESPONSE_MODALITIES] == [3]
    assert gc[GenerationConfigIndex.SPEECH_CONFIG] == [[["Kore"]]]


def test_model_discovery_voice_and_video_capabilities():
    tts_row: list[Any] = [None] * 80
    tts_row[0] = "models/gemini-3.8-flash-tts"
    tts_row[2] = "001"
    tts_row[3] = "Gemini 3.8 Flash TTS"
    tts_row[4] = "TTS Model"
    tts_row[5] = 8192
    tts_row[6] = 16384
    tts_row[7] = ["generateContent"]
    tts_row[8] = 1.0
    tts_row[9] = 0.95
    tts_row[10] = 64
    tts_row[56] = []
    tts_row[64] = [37, 85]  # speech_route, speech_metadata
    tts_row[66] = [["Zephyr"], ["Kore"], ["Puck"]]  # field 67 voices

    omni_row: list[Any] = [None] * 80
    omni_row[0] = "models/gemini-omni-1.1-flash"
    omni_row[2] = "001"
    omni_row[3] = "Gemini Omni 1.1 Flash"
    omni_row[4] = "Video Model"
    omni_row[5] = 131072
    omni_row[6] = 65536
    omni_row[7] = ["generateContent"]
    omni_row[8] = 1.0
    omni_row[9] = 0.95
    omni_row[10] = 64
    omni_row[56] = []
    omni_row[64] = [20]  # video_route

    raw_response = [[tts_row, omni_row]]
    parsed = model_discovery._parse_raw_models(raw_response)
    assert len(parsed) == 2

    tts_m = next(m for m in parsed if m["id"] == "gemini-3.8-flash-tts")
    assert tts_m["category"] == "audio"
    assert tts_m["is_audio_model"] is True
    assert tts_m["voices"] == ["Zephyr", "Kore", "Puck"]
    caps = tts_m["capabilities"]
    assert isinstance(caps, list) and "speech_route" in caps

    omni_m = next(m for m in parsed if m["id"] == "gemini-omni-1.1-flash")
    assert omni_m["category"] == "video"
    assert omni_m["is_video_model"] is True
    omni_caps = omni_m["capabilities"]
    assert isinstance(omni_caps, list) and "video_route" in omni_caps
