"""Shared utility helpers."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import time
from pathlib import Path


def atomic_write_json(path: Path | str, data: object) -> None:
    """原子写入 JSON 文件（写唯一临时文件后原子替换，防止写穿或损坏）。"""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(f".tmp.{os.getpid()}_{time.time_ns()}")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(target_path)
    except Exception:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


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
            decoded.append(
                {
                    "mime": str(img.get("mime", "image/jpeg")),
                    "bytes": data,
                    "size": len(data),
                }
            )
        except Exception:
            pass
    return decoded
