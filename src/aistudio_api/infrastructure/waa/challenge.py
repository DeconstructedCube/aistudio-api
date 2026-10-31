"""Google WAA (Web Authentication / Attestation) challenge protocol client.

Handles Google Waa/Create RPC calls, Caesar-shift challenge decoding, and
interpreter bytecode caching.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from aistudio_api.infrastructure.account.cookie_parser import (
    calculate_sapisid_hash,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("waa.challenge")

WAA_CREATE_URL = (
    "https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Create"
)
WAA_REQUEST_KEY = "lmnUSbltwc5ULv48iKLX"
WAA_API_KEY = "AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE"
AISTUDIO_ORIGIN = "https://aistudio.google.com"

# In-memory interpreter code cache keyed by interpreter_hash
_INTERPRETER_CACHE: dict[str, str] = {}


@dataclass(slots=True)
class Challenge:
    """Parsed challenge payload returned by Google Waa/Create."""

    message_id: str
    interpreter_javascript: str
    interpreter_url: str
    interpreter_hash: str
    program: str
    global_name: str
    client_experiments_state_blob: str


def compute_interpreter_hash(source_bytes: bytes) -> str:
    """Calculate raw URL-safe unpadded base64 SHA-256 digest of interpreter code."""
    digest = hashlib.sha256(source_bytes).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def parse_challenge_payload(raw_bytes: bytes) -> Challenge:
    """Parse raw Waa/Create JSON response.

    Google wraps the challenge in an outer JSON array. Field [1] is a base64
    string whose decoded bytes are shifted by +97 (Caesar cipher modulo 256).
    """
    outer = json.loads(raw_bytes)
    if not isinstance(outer, list) or not outer:
        raise ValueError("Invalid Waa/Create response: expected JSON array")

    encoded_str = ""
    if len(outer) > 1 and isinstance(outer[1], str) and outer[1]:
        encoded_str = outer[1]

    if encoded_str:
        decoded = bytearray(base64.b64decode(encoded_str))
        for i in range(len(decoded)):
            decoded[i] = (decoded[i] + 97) & 0xFF
        fields: list[Any] = json.loads(decoded.decode("utf-8"))
    elif outer[0] and isinstance(outer[0], list):
        fields = outer[0]
    else:
        raise ValueError("Waa/Create response missing challenge data")

    if len(fields) < 8:
        raise ValueError(f"Waa/Create challenge insufficient fields: {len(fields)}")

    def _extract_str(val: Any) -> str:
        if isinstance(val, str):
            return val
        return ""

    def _extract_first_str(val: Any) -> str:
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item:
                    return item
        elif isinstance(val, str):
            return val
        return ""

    message_id = _extract_str(fields[0])
    interp_js = _extract_first_str(fields[1])
    interp_path = _extract_first_str(fields[2])
    interp_hash = _extract_str(fields[3])
    program = _extract_str(fields[4])
    global_name = _extract_str(fields[5])
    experiments_blob = _extract_str(fields[7]) if len(fields) > 7 else ""

    interpreter_url = ""
    if interp_path:
        if interp_path.startswith("//"):
            interpreter_url = "https:" + interp_path
        elif interp_path.startswith("https://"):
            interpreter_url = interp_path
        else:
            interpreter_url = "https://" + interp_path.lstrip("/")

    if not interp_hash or not program or not global_name:
        raise ValueError("Waa/Create challenge missing required hash, program, or globalName")

    return Challenge(
        message_id=message_id,
        interpreter_javascript=interp_js,
        interpreter_url=interpreter_url,
        interpreter_hash=interp_hash,
        program=program,
        global_name=global_name,
        client_experiments_state_blob=experiments_blob,
    )


async def fetch_challenge(
    *,
    cookies: dict[str, str] | None = None,
    auth_user: str = "0",
    refresh: list[str] | None = None,
    proxy_url: str | None = None,
    timeout: float = 15.0,
) -> Challenge:
    """Invoke Google Waa/Create RPC endpoint and parse the returned Challenge."""
    request_body: list[str] = [WAA_REQUEST_KEY]
    if refresh:
        request_body.extend(refresh)

    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json+protobuf",
        "X-Goog-Api-Key": WAA_API_KEY,
        "X-Goog-AuthUser": str(auth_user or "0"),
        "X-User-Agent": "grpc-web-javascript/0.1",
        "Origin": AISTUDIO_ORIGIN,
        "Referer": f"{AISTUDIO_ORIGIN}/",
    }

    if cookies:
        auth_header = calculate_sapisid_hash(cookies, origin=AISTUDIO_ORIGIN)
        if auth_header:
            headers["Authorization"] = auth_header
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
        if cookie_header:
            headers["Cookie"] = cookie_header

    body_bytes = json.dumps(request_body, separators=(",", ":")).encode("utf-8")
    logger.debug("Calling Waa/Create RPC: auth_user=%s, body_len=%d", auth_user, len(body_bytes))

    async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
        response = await client.post(WAA_CREATE_URL, headers=headers, content=body_bytes)
        if response.status_code != 200:
            raise RuntimeError(
                f"Waa/Create returned HTTP {response.status_code}: {response.text[:200]}"
            )
        raw_bytes = response.content

    challenge = parse_challenge_payload(raw_bytes)
    logger.info(
        "Successfully decoded WAA Challenge: id=%s, hash=%s, program_len=%d, global=%s",
        challenge.message_id,
        challenge.interpreter_hash,
        len(challenge.program),
        challenge.global_name,
    )
    return challenge


async def fetch_interpreter_code(
    challenge: Challenge,
    *,
    cache_dir: Path | None = None,
    proxy_url: str | None = None,
    timeout: float = 15.0,
) -> str:
    """Download interpreter JavaScript if needed and verify its SHA-256 hash."""
    h = challenge.interpreter_hash
    if h in _INTERPRETER_CACHE:
        return _INTERPRETER_CACHE[h]

    # Check disk cache
    if cache_dir:
        disk_file = cache_dir / f"{h}.js"
        if disk_file.is_file():
            content = disk_file.read_text(encoding="utf-8")
            if compute_interpreter_hash(content.encode("utf-8")) == h:
                _INTERPRETER_CACHE[h] = content
                return content

    # Check inline bytecode in challenge
    if challenge.interpreter_javascript:
        content = challenge.interpreter_javascript
        actual_hash = compute_interpreter_hash(content.encode("utf-8"))
        if actual_hash == h:
            _INTERPRETER_CACHE[h] = content
            if cache_dir:
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    (cache_dir / f"{h}.js").write_text(content, encoding="utf-8")
                except Exception as e:
                    logger.debug("Failed to write interpreter cache: %s", e)
            return content

    # Download from CDN
    url = challenge.interpreter_url or f"https://www.google.com/js/bg/{h}.js"
    logger.debug("Downloading interpreter JS from %s", url)
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download interpreter JS from {url}: HTTP {resp.status_code}")
        source_bytes = resp.content

    actual_hash = compute_interpreter_hash(source_bytes)
    if actual_hash != h:
        raise ValueError(
            f"Interpreter SHA-256 hash mismatch! Expected {h}, got {actual_hash}"
        )

    content = source_bytes.decode("utf-8")
    _INTERPRETER_CACHE[h] = content
    if cache_dir:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{h}.js").write_text(content, encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to write interpreter cache: %s", e)

    return content
