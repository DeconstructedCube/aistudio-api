"""Unit tests to validate JavaScript syntax of browser-injected scripts.

Ensures that even in environments where CDP is mocked (such as Termux),
syntax errors, missing braces, or evaluation wrapper mismatches in
src/aistudio_api/infrastructure/browser/js/*.js are caught during `pytest`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aistudio_api.infrastructure.browser.scripts import (
    CHECK_IDENTITY_JS,
    DIALOG_CLEANUP_JS,
    DOM_GC_CLEANUP_JS,
    EXTRACT_EMAIL_JS,
    HOOKED_REQUEST_JS,
    INSTALL_HOOKS_JS,
    SNAPSHOT_GENERATE_JS,
    STOP_GENERATION_JS,
    STREAM_CLEANUP_JS,
    STREAMING_INIT_JS,
)

JS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "aistudio_api"
    / "infrastructure"
    / "browser"
    / "js"
)


def _check_pure_python_bracket_balance(code: str) -> None:
    """Validate brace, bracket, and parenthesis balance while ignoring strings/comments."""
    stack: list[tuple[str, int]] = []
    pairs = {"}": "{", ")": "(", "]": "["}
    i = 0
    n = len(code)
    line = 1

    while i < n:
        char = code[i]
        if char == "\n":
            line += 1
            i += 1
            continue

        # Skip line comments
        if char == "/" and i + 1 < n and code[i + 1] == "/":
            i += 2
            while i < n and code[i] != "\n":
                i += 1
            continue

        # Skip block comments
        if char == "/" and i + 1 < n and code[i + 1] == "*":
            i += 2
            while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                if code[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue

        # Skip regex literals
        if char == "/":
            prev = code[:i].rstrip()
            if prev and prev[-1] in (
                "(",
                "=",
                ":",
                ",",
                "!",
                "&",
                "|",
                "?",
                ";",
                "{",
                "[",
            ):
                i += 1
                while i < n and code[i] != "/":
                    if code[i] == "\\":
                        i += 2
                        continue
                    if code[i] == "\n":
                        break
                    i += 1
                i += 1
                continue

        # Skip string literals ('...', "...", `...`)
        if char in ("'", '"', "`"):
            quote = char
            i += 1
            while i < n:
                if code[i] == "\\":
                    i += 2
                    continue
                if code[i] == "\n" and quote != "`":
                    break
                if code[i] == quote:
                    i += 1
                    break
                i += 1
            continue

        # Check brackets
        if char in ("{", "(", "["):
            stack.append((char, line))
        elif char in ("}", ")", "]"):
            expected = pairs[char]
            if not stack:
                raise AssertionError(
                    f"Unexpected closing '{char}' at line {line} (no matching open '{expected}')"
                )
            opened, open_line = stack.pop()
            if opened != expected:
                raise AssertionError(
                    f"Mismatched bracket: opened '{opened}' at line {open_line}, closed with '{char}' at line {line}"
                )

        i += 1

    if stack:
        opened, open_line = stack[-1]
        raise AssertionError(
            f"Unclosed '{opened}' opened at line {open_line} (missing closing bracket at EOF)"
        )


def test_pure_python_js_bracket_balance():
    """Verify all browser JS scripts have balanced brackets (works offline/Termux)."""
    assert JS_DIR.is_dir(), f"JS directory not found: {JS_DIR}"
    js_files = list(JS_DIR.glob("*.js"))
    assert len(js_files) >= 10, f"Expected at least 10 JS files, found {len(js_files)}"

    for js_file in js_files:
        content = js_file.read_text(encoding="utf-8")
        try:
            _check_pure_python_bracket_balance(content)
        except AssertionError as exc:
            pytest.fail(f"Syntax/bracket error in {js_file.name}: {exc}")


def test_js_ast_syntax_with_external_runtime():
    """If node or bun is installed on the host, perform native AST syntax check."""
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        pytest.skip("Neither bun nor node found in PATH; skipping external AST check.")

    is_bun = "bun" in Path(runtime).name.lower()
    for js_file in JS_DIR.glob("*.js"):
        if is_bun:
            cmd = [runtime, "build", str(js_file), "--no-bundle"]
        else:
            cmd = [runtime, "-c", str(js_file)]

        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, (
            f"{runtime} syntax check failed for {js_file.name}:\n{res.stderr}"
        )


def test_evaluate_wrapper_expressions_parse_cleanly():
    """Test wrapping each script inside an expression as CDP evaluate() does."""
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        pytest.skip("Neither bun nor node found in PATH; skipping evaluate check.")

    scripts = [
        ("INSTALL_HOOKS_JS", INSTALL_HOOKS_JS, None),
        ("SNAPSHOT_GENERATE_JS", SNAPSHOT_GENERATE_JS, "sample_hash"),
        ("STREAMING_INIT_JS", STREAMING_INIT_JS, {"rid": "r1", "url": "http://x"}),
        ("STREAM_CLEANUP_JS", STREAM_CLEANUP_JS, "r1"),
        ("DIALOG_CLEANUP_JS", DIALOG_CLEANUP_JS, None),
        ("DOM_GC_CLEANUP_JS", DOM_GC_CLEANUP_JS, None),
        ("CHECK_IDENTITY_JS", CHECK_IDENTITY_JS, {"email": "a@b.com"}),
        ("STOP_GENERATION_JS", STOP_GENERATION_JS, None),
        ("EXTRACT_EMAIL_JS", EXTRACT_EMAIL_JS, None),
        ("HOOKED_REQUEST_JS", HOOKED_REQUEST_JS, None),
    ]

    for name, script_text, arg in scripts:
        expr = script_text.strip()
        wrapped = f"({expr})({json.dumps(arg)})" if arg is not None else f"({expr})()"

        prelude = "globalThis.window = globalThis; globalThis.document = { querySelector: () => null, querySelectorAll: () => [] };"
        code = f"{prelude} try {{ new Function({wrapped!r}); }} catch (e) {{ if (e instanceof SyntaxError) throw e; }}"
        cmd = [runtime, "-e", code]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, (
            f"SyntaxError in evaluate() wrapper for {name}:\n{res.stderr}"
        )
