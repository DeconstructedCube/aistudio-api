"""Incremental parser for AI Studio streaming responses."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import suppress

from aistudio_api.infrastructure.gateway.wire_parser import parse_response_chunk

XSSI_PREFIX = ")]}'"


class IncrementalJSONStreamParser:
    def __init__(self):
        self.buffer = ""
        self.depth = 0
        self.in_string = False
        self.escape = False
        self.chunk_start = None
        self.preamble_skipped = False
        self._pos = 0

    def feed(self, data: str) -> Generator:
        self.buffer += data

        while True:
            if not self.preamble_skipped:
                if self.buffer.startswith(XSSI_PREFIX):
                    self.buffer = self.buffer[len(XSSI_PREFIX) :].lstrip()
                elif XSSI_PREFIX.startswith(self.buffer):
                    break
                self.preamble_skipped = True

            made_progress = False
            while self._pos < len(self.buffer):
                ch = self.buffer[self._pos]

                if self.escape:
                    self.escape = False
                    self._pos += 1
                    continue

                if ch == "\\" and self.in_string:
                    self.escape = True
                    self._pos += 1
                    continue

                if ch == '"' and not self.escape:
                    self.in_string = not self.in_string
                    self._pos += 1
                    continue

                if self.in_string:
                    self._pos += 1
                    continue

                if ch == "[":
                    self.depth += 1
                    if self.depth == 3 and self.chunk_start is None:
                        self.chunk_start = self._pos
                elif ch == "]":
                    self.depth -= 1
                    if self.depth == 2 and self.chunk_start is not None:
                        chunk_str = self.buffer[self.chunk_start : self._pos + 1]
                        with suppress(json.JSONDecodeError):
                            yield json.loads(chunk_str)
                        self.buffer = self.buffer[self._pos + 1 :]
                        self._pos = 0
                        self.chunk_start = None
                        made_progress = True
                        continue

                self._pos += 1

            if not made_progress:
                break

    def finish(self) -> Generator[list[object], None, None]:
        yield from ()


def classify_chunk(chunk: list) -> tuple[str, object]:
    candidate = parse_response_chunk(chunk)
    if candidate.thinking:
        return ("thinking", candidate.thinking)
    if candidate.function_calls:
        return ("tool_calls", candidate.function_calls)
    if candidate.text:
        return ("body", candidate.text)
    if candidate.thought_signature:
        return ("thought_signature", candidate.thought_signature)
    return ("unknown", "")
