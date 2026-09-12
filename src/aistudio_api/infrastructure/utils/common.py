"""Shared utility helpers."""

from __future__ import annotations

import base64
import json
import logging
import reprlib
from typing import Any

logger = logging.getLogger("aistudio")


def get_nested_value(
    data: Any,
    path: list[int | str],
    default: Any = None,
    verbose: bool = False,
) -> Any:
    current = data
    for i, key in enumerate(path):
        found = False
        if isinstance(key, int):
            if isinstance(current, list) and -len(current) <= key < len(current):
                current = current[key]
                found = True
        elif isinstance(key, str):
            if isinstance(current, dict) and key in current:
                current = current[key]
                found = True

        if not found:
            if verbose:
                logger.debug(
                    "Safe navigation: path %s ended at index %s (key %r), returning default. Context: %s",
                    path,
                    i,
                    key,
                    reprlib.repr(current),
                )
            return default

    return current if current is not None else default


def extract_outer_json(raw: str) -> list[Any]:
    stripped = raw.strip()
    if not stripped:
        return []

    if stripped.startswith(")]}'"):
        stripped = stripped[4:].lstrip()

    try:
        return [json.loads(stripped)]
    except json.JSONDecodeError:
        pass

    results = []
    for line in stripped.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


def extract_all_strings(obj: Any, min_len: int = 5) -> list[str]:
    results = []
    if isinstance(obj, str) and len(obj) >= min_len:
        results.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            results.extend(extract_all_strings(item, min_len))
    return results


def find_base64_images(obj: Any) -> list[dict]:
    if isinstance(obj, list):
        if (
            len(obj) >= 2
            and isinstance(obj[0], str)
            and obj[0].startswith("image/")
            and isinstance(obj[1], str)
            and len(obj[1]) > 100
        ):
            return [{"mime": obj[0], "data": obj[1]}]
        found = []
        for item in obj:
            found.extend(find_base64_images(item))
        return found
    return []


def decode_base64_images(images: list[dict]) -> list[dict]:
    decoded = []
    for img in images:
        try:
            data = base64.b64decode(img["data"])
            decoded.append({"mime": img["mime"], "bytes": data, "size": len(data)})
        except Exception:
            pass
    return decoded

