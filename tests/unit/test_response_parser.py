import json
from pathlib import Path

from aistudio_api.domain.models import parse_chunk_usage, parse_response_chunk, parse_text_output
from aistudio_api.infrastructure.gateway.stream_parser import IncrementalJSONStreamParser, classify_chunk


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_parse_text_output_from_stream_bundle():
    raw = (FIXTURES / "test_output.json").read_text()
    output = parse_text_output(raw)

    assert output.text == "你好！有什么我可以帮你的吗？"
    assert output.thinking.startswith('The user said "你好"')
    assert "Option 3 (Friendly)" in output.thinking
    assert output.usage["prompt_tokens"] == 5
    assert output.usage["completion_tokens"] == 161
    assert output.usage["total_tokens"] == 166
    details = output.usage.get("completion_tokens_details")
    assert isinstance(details, dict)
    assert details.get("reasoning_tokens") == 153
    assert output.response_id
    assert output.candidates[0].finish_reason == 1


def test_parse_response_chunk_and_classify_chunk():
    raw = json.loads((FIXTURES / "test_output.json").read_text())
    final_chunk = raw[0][-1]

    candidate = parse_response_chunk(final_chunk)
    usage = parse_chunk_usage(final_chunk)
    assert candidate.finish_reason == 1
    assert candidate.safety_ratings
    assert usage["prompt_tokens"] == 5
    assert usage["completion_tokens"] == 161
    usage_details = usage.get("completion_tokens_details")
    assert isinstance(usage_details, dict)
    assert usage_details.get("reasoning_tokens") == 153

    ctype, text = classify_chunk(raw[0][1])
    assert ctype == "thinking"
    assert isinstance(text, str) and 'standard Chinese greeting meaning "Hello."' in text


def test_stream_parser_extracts_real_chunks():
    raw = (FIXTURES / "test_output.json").read_text()
    parser = IncrementalJSONStreamParser()

    chunks = list(parser.feed(raw))
    assert len(chunks) == 10
    assert classify_chunk(chunks[2])[0] == "thinking"
    assert classify_chunk(chunks[7])[0] == "body"


def test_parse_text_output_handles_double_wrapped_chunk_bundle():
    raw = json.dumps(
        [
            [
                [
                    [[[[[None, "先想一会儿", None, None, None, None, None, None, None, None, None, None, 1]], "model"]]],
                    None,
                    [10, None, 10, None, [[1, 10]]],
                    None,
                    None,
                    None,
                    None,
                    "resp_test",
                ],
                [
                    [[[[[None, "答案", None, None, None, None, None, None, None, None, None, None, 0]], "model"]]],
                    None,
                    [10, 2, 17, None, [[1, 10]], None, None, None, None, 5],
                    None,
                    None,
                    None,
                    None,
                    "resp_test",
                ],
            ]
        ]
    )

    output = parse_text_output(raw)

    assert output.thinking == "先想一会儿"
    assert output.text == "答案"
    assert output.usage["prompt_tokens"] == 10
    assert output.usage["completion_tokens"] == 7
    assert output.usage["total_tokens"] == 17
    details = output.usage.get("completion_tokens_details")
    assert isinstance(details, dict)
    assert details.get("reasoning_tokens") == 5
    assert output.response_id == "resp_test"
