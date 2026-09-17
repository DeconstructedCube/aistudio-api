"""Shared utility helpers."""

from __future__ import annotations

import base64
import json


def extract_outer_json(raw: str) -> list[object]:
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



def decode_base64_images(images: list[dict[str, object]]) -> list[dict[str, object]]:
    decoded = []
    for img in images:
        try:
            data = base64.b64decode(str(img["data"]))
            decoded.append({"mime": str(img.get("mime", "image/jpeg")), "bytes": data, "size": len(data)})
        except Exception:
            pass
    return decoded
