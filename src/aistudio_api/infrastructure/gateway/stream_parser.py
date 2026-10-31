"""Incremental parser for AI Studio streaming responses with truncated JSON repair."""

from __future__ import annotations

import json
import re
from collections.abc import Generator

from aistudio_api.infrastructure.gateway.wire_parser import (
    WIRE_RESPONSE_ID_INDEX,
    parse_chunk_usage,
    parse_response_chunk,
)

XSSI_PREFIX = ")]}'"

_TRAILING_JUNK = " \t\r\n,"
_INCOMPLETE_UNICODE_RE = re.compile(r"\\u[0-9a-fA-F]{0,3}$")

_CONTROL_ESCAPES = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _escape_control_char(ch: str) -> str:
    """把字符串里的裸控制符转成合法 JSON 转义（截断流里常见）。"""
    return _CONTROL_ESCAPES.get(ch) or f"\\u{ord(ch):04x}"


def _trim_incomplete_unicode_escape(text: str) -> str:
    """裁掉末尾被切断的 \\uXXXX（不足 4 位十六进制）。"""
    match = _INCOMPLETE_UNICODE_RE.search(text)
    if not match:
        return text
    backslashes_before = 0
    index = match.start() - 1
    while index >= 0 and text[index] == "\\":
        backslashes_before += 1
        index -= 1
    if backslashes_before % 2 == 1:
        return text
    return text[: match.start()]


def _close_json_fragment(fragment: str) -> str | None:
    """补全一个截断的 JSON 片段：闭合字符串 / 数组 / 对象、处理末尾反斜杠。"""
    if not isinstance(fragment, str) or not fragment.strip():
        return None

    closers: list[str] = []
    out: list[str] = []
    in_string = False
    escape = False

    for ch in fragment:
        if in_string:
            if escape:
                escape = False
                out.append(ch)
            elif ch == "\\":
                escape = True
                out.append(ch)
            elif ch == '"':
                in_string = False
                out.append(ch)
            elif ch < " ":
                out.append(_escape_control_char(ch))
            else:
                out.append(ch)
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "[":
            closers.append("]")
            out.append(ch)
        elif ch == "{":
            closers.append("}")
            out.append(ch)
        elif ch in "]}":
            if closers and closers[-1] == ch:
                closers.pop()
            out.append(ch)
        else:
            out.append(ch)

    if escape:
        out.pop()
    text = "".join(out)
    if in_string:
        text = _trim_incomplete_unicode_escape(text)
        text += '"'
    text = text.rstrip(_TRAILING_JUNK)
    text += "".join(reversed(closers))
    return text


def _cut_at_last_top_level_comma(fragment: str) -> str:
    """丢掉最深未闭合容器的半个元素，保留已收到的兄弟元素。"""
    in_string = False
    escape = False
    commas: list[int] = []
    for index, ch in enumerate(fragment):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            commas.append(-1)
        elif ch in "]}":
            if commas:
                commas.pop()
        elif ch == "," and commas:
            commas[-1] = index
    for comma in reversed(commas):
        if comma > 0:
            return fragment[:comma]
    return ""


def _repair_truncated_json(fragment: str) -> object | None:
    """把截断的 JSON 片段补全成可解析对象；救不回来返回 None。"""
    if not isinstance(fragment, str) or not fragment.strip():
        return None

    candidate = fragment
    for _ in range(8):
        repaired = _close_json_fragment(candidate)
        if repaired is not None:
            try:
                return json.loads(repaired)
            except (TypeError, ValueError):
                pass
        candidate = _cut_at_last_top_level_comma(candidate)
        if not candidate:
            break
    return None


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

    @staticmethod
    def _parse_chunk(chunk_str: str) -> object | None:
        try:
            return json.loads(chunk_str)
        except (TypeError, ValueError):
            return _repair_truncated_json(chunk_str)

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
                        parsed = self._parse_chunk(chunk_str)
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
        """流结束：把缓冲区里最后一个不完整的分块修复后交出去，防止丢字/丢finish/丢usage。"""
        fragment = ""
        if self.chunk_start is not None:
            fragment = self.buffer[self.chunk_start :]
        self.buffer = ""
        self._pos = 0
        self.chunk_start = None
        self.depth = 0
        self.in_string = False
        self.escape = False

        if not fragment.strip():
            return
        repaired = _repair_truncated_json(fragment)
        if not isinstance(repaired, list):
            return
        if parse_chunk_usage(repaired):
            yield repaired
            return
        if (
            len(repaired) > WIRE_RESPONSE_ID_INDEX
            and isinstance(repaired[WIRE_RESPONSE_ID_INDEX], str)
            and repaired[WIRE_RESPONSE_ID_INDEX]
        ):
            yield repaired
            return
        container = repaired[0] if repaired else None
        if isinstance(container, list) and container:
            candidate = parse_response_chunk(repaired)
            if (
                candidate.text
                or candidate.thinking
                or candidate.reasoning_images
                or candidate.images
                or candidate.function_calls
                or candidate.thought_signature
                or candidate.finish_reason is not None
                or candidate.finish_message
                or candidate.safety_ratings
            ):
                yield repaired


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


__all__ = [
    "XSSI_PREFIX",
    "IncrementalJSONStreamParser",
    "classify_chunk",
]
