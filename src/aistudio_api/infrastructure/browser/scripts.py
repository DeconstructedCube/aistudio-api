"""Browser automation JavaScript scripts and parameterization utilities."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

_JS_DIR = Path(__file__).resolve().parent / "js"
_GZIP_MIN_BYTES = 96 * 1024


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
EXTRACT_EMAIL_JS = _load_js("extract_email.js")


def build_streaming_init_args(
    *, url: str, headers: dict[str, str], body: str, rid: str
) -> dict[str, object]:
    """Prepare argument object for STREAMING_INIT_JS evaluation."""
    return {
        **build_hooked_request_args(url=url, headers=headers, body=body),
        "rid": rid,
    }


def build_hooked_request_args(
    *, url: str, headers: dict[str, str], body: str
) -> dict[str, object]:
    """Prepare argument object for HOOKED_REQUEST_JS evaluation."""
    args: dict[str, object] = {
        "url": url,
        "headers": headers,
        "body": body,
    }
    if any(
        key.lower() == "content-encoding" and value.lower() != "identity"
        for key, value in headers.items()
    ):
        return args
    raw = body.encode("utf-8")
    if len(raw) >= _GZIP_MIN_BYTES:
        args["body"] = base64.b64encode(gzip.compress(raw, compresslevel=6)).decode(
            "ascii"
        )
        args["headers"] = {
            **{
                key: value
                for key, value in headers.items()
                if key.lower() != "content-encoding"
            },
            "Content-Encoding": "gzip",
        }
        args["gzip"] = True
    return args


def build_selector_match_js(selector: str) -> str:
    """Generate in-page element query snippet for standard CSS, text=, or :has-text() selector."""
    return f"""(() => {{
        const sel = {json.dumps(selector)};
        if (sel.includes(':has-text(')) {{
            const m = sel.match(/^(.*?):has-text\\(['"](.*?)['"]\\)(.*)$/);
            if (m) {{
                const tag = m[1].trim() || '*';
                const text = m[2];
                return Array.from(document.querySelectorAll(tag)).find(e => (e.textContent || '').includes(text)) || null;
            }}
        }}
        if (sel.startsWith('text=')) {{
            const text = sel.slice(5).trim();
            return Array.from(document.querySelectorAll('*')).find(e => (e.textContent || '').includes(text)) || null;
        }}
        return document.querySelector(sel);
    }})()"""
