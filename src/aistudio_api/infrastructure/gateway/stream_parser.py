"""Incremental parser for AI Studio streaming responses."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import suppress

from aistudio_api.infrastructure.gateway.wire_parser import parse_response_chunk

XSSI_PREFIX = ")]}'"


class IncrementalJSONStreamParser:
    """增量流式 JSON 数组提取器。

    Google AI Studio 流式响应的外层结构为 XSSI 前缀后跟随二维/三维数组：
    `)]}'\\n[[ [candidate, finish_reason], null, usage, ..., response_id ]]`
    顶层包裹数组为深度 1，中间批次数组为深度 2，内部独立的每个分块为深度 3。
    因此本状态机在深度到达 3 时标记分块起始，退回深度 2 时切出完整 chunk 并反序列化。
    """

    def __init__(self) -> None:
        self.buffer: str = ""
        self.depth: int = 0
        self.in_string: bool = False
        self.escape: bool = False
        self.chunk_start: int | None = None
        self.preamble_skipped: bool = False
        self._pos: int = 0

    def feed(self, data: str) -> Generator[list[object], None, None]:
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
                        parsed: object = None
                        with suppress(json.JSONDecodeError):
                            parsed = json.loads(chunk_str)
                        self.buffer = self.buffer[self._pos + 1 :]
                        self._pos = 0
                        self.chunk_start = None
                        made_progress = True
                        if isinstance(parsed, list):
                            yield parsed
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
