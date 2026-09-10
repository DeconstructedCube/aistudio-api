"""Unit tests for direct Chromium process launcher and args."""

from __future__ import annotations

import os

from aistudio_api.infrastructure.browser.browser_engine import (
    _derive_stable_fingerprint_seed,
    build_chromium_args,
    find_chromium_executable,
)


def test_derive_stable_fingerprint_seed_is_deterministic():
    seed1 = _derive_stable_fingerprint_seed("/tmp/profile1")
    seed2 = _derive_stable_fingerprint_seed("/tmp/profile1")
    seed3 = _derive_stable_fingerprint_seed("/tmp/profile2")

    assert seed1 == seed2
    assert seed1 != seed3
    assert 10000 <= seed1 <= 99999


def test_build_chromium_args_contains_stealth_and_mobile_optimizations():
    args = build_chromium_args(
        port=9222,
        user_data_dir="/tmp/test_profile",
        headless=True,
    )

    args_str = " ".join(args)
    assert "--remote-debugging-port=9222" in args_str
    assert "--user-data-dir=/tmp/test_profile" in args_str
    assert "--no-sandbox" in args_str
    assert "--disable-dev-shm-usage" in args_str
    assert "--disable-gpu" in args_str
    assert "--in-process-gpu" in args_str
    assert "--js-flags=--max-old-space-size=128" in args_str
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args_str
    assert "--headless=new" in args_str


def test_find_chromium_executable_locates_binary():
    executable = find_chromium_executable()
    assert os.path.exists(executable)
    assert os.access(executable, os.X_OK)
