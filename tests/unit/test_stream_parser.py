from aistudio_api.infrastructure.gateway.stream_parser import (
    IncrementalJSONStreamParser,
)


def test_stream_parser_extracts_chunk_from_fragmented_input():
    parser = IncrementalJSONStreamParser()
    pieces = ['[[[null,"he', 'llo"]]]']

    chunks = []
    for piece in pieces:
        chunks.extend(list(parser.feed(piece)))

    assert chunks == [[None, "hello"]]


def test_stream_parser_handles_fragmented_xssi_preamble():
    parser = IncrementalJSONStreamParser()
    pieces = [")]", "}'\n", '[[[null,"hello"]]]']

    chunks = []
    for piece in pieces:
        chunks.extend(list(parser.feed(piece)))

    assert chunks == [[None, "hello"]]
