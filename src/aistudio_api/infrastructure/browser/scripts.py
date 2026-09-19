"""Browser automation JavaScript scripts and parameterization utilities."""

from __future__ import annotations

from pathlib import Path

_JS_DIR = Path(__file__).resolve().parent / "js"


def _load_js(filename: str) -> str:
    return (_JS_DIR / filename).read_text(encoding="utf-8")


INSTALL_HOOKS_JS = _load_js("install_hooks.js")
DIALOG_CLEANUP_JS = _load_js("dialog_cleanup.js")
STREAMING_INIT_JS = _load_js("streaming_init.js")
STREAM_CLEANUP_JS = _load_js("stream_cleanup.js")
HOOKED_REQUEST_JS = _load_js("hooked_request.js")
SNAPSHOT_GENERATE_JS = _load_js("snapshot_generate.js")
CHECK_IDENTITY_JS = _load_js("check_identity.js")
STOP_GENERATION_JS = _load_js("stop_generation.js")
DOM_GC_CLEANUP_JS = _load_js("dom_gc_cleanup.js")


def build_streaming_init_args(
    *, url: str, headers: dict[str, str], body: str, timeout_s: float, rid: str
) -> dict[str, object]:
    """Prepare argument object for STREAMING_INIT_JS evaluation."""
    return {
        "url": url,
        "headers": headers,
        "body": body,
        "timeout": timeout_s,
        "rid": rid,
    }


def build_hooked_request_args(
    *, url: str, headers: dict[str, str], body: str, timeout_s: float
) -> dict[str, object]:
    """Prepare argument object for HOOKED_REQUEST_JS evaluation."""
    return {
        "url": url,
        "headers": headers,
        "body": body,
        "timeout": timeout_s,
    }
