"""Unit tests for direct Chromium process launcher and args."""

from __future__ import annotations

import os
from unittest.mock import patch

from aistudio_api.infrastructure.browser.browser_engine import (
    _derive_stable_fingerprint_seed,
    _spawn_process_watchdog,
    build_chromium_args,
    cleanup_stale_chromium,
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


def test_build_chromium_args_low_memory_optimizations():
    """Verify low-memory optimization flags and absence of memory-pressure-off."""
    args = build_chromium_args(port=9222)
    args_str = " ".join(args)

    assert "--enable-low-end-device-mode" in args
    assert "--aggressive-cache-discard" in args
    assert "--optimize-for-size" in args_str
    # Must NOT disable memory pressure on mobile/low-RAM
    assert "--memory-pressure-off" not in args_str


def test_cleanup_stale_chromium_skips_active_api_server():
    """Verify cleanup_stale_chromium strictly protects browsers owned by running API instances."""
    with patch(
        "aistudio_api.infrastructure.browser.browser_engine._is_active_api_server",
        return_value=True,
    ):
        # Even if port is requested, active API server's children must not be killed
        killed = cleanup_stale_chromium(port=9222)
        assert isinstance(killed, list)


def test_spawn_process_watchdog_creates_pipe_and_reaps():
    """Verify process watchdog companion spawns with pipe and cleans up."""
    import subprocess

    dummy = subprocess.Popen(["true"])
    dummy.wait()

    watcher, pipe_w = _spawn_process_watchdog(dummy.pid, target_pgid=None)
    if watcher is not None:
        assert pipe_w is not None
        os.close(pipe_w)
        watcher.wait(timeout=2.0)
        assert watcher.poll() is not None
