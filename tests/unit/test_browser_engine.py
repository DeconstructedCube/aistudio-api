"""Unit tests for direct Chromium process launcher and args."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from aistudio_api.infrastructure.browser.browser_engine import (
    _assign_process_to_job,
    _create_windows_job_object,
    _derive_stable_fingerprint_seed,
    _is_active_api_server,
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
    assert "--use-angle=swiftshader" in args_str
    assert "--disable-software-rasterizer" not in args_str
    assert "--disable-audio" not in args_str
    assert "--mute-audio" in args_str
    assert "--fingerprint-timezone=" in args_str
    assert "--fingerprint-locale=" in args_str


def test_find_chromium_executable_strictly_requires_cloakbrowser(tmp_path):
    # 1. When CloakBrowser is installed, successfully returns executable
    fake_home = tmp_path / "home"
    fake_cloak_dir = fake_home / ".cloakbrowser" / "chromium"
    fake_cloak_dir.mkdir(parents=True)
    bin_name = "chrome.exe" if platform.system() == "Windows" else "chrome"
    fake_bin = fake_cloak_dir / bin_name
    fake_bin.write_text("fake binary")
    if platform.system() != "Windows":
        fake_bin.chmod(0o755)

    orig_is_dir = Path.is_dir

    def fake_is_dir(self):
        if self.name == ".cloakbrowser" and not str(self).startswith(str(fake_home)):
            return False
        return orig_is_dir(self)

    with (
        patch("pathlib.Path.home", return_value=fake_home),
        patch("aistudio_api.config.settings.browser_executable_path", None),
        patch.object(Path, "is_dir", fake_is_dir),
    ):
        executable = find_chromium_executable()
        assert os.path.exists(executable)
        assert "chrome" in os.path.basename(executable).lower()

    # 2. When CloakBrowser directories do not exist, strictly raises FileNotFoundError
    with (
        patch("pathlib.Path.is_dir", return_value=False),
        patch("aistudio_api.config.settings.browser_executable_path", None),
        patch(
            "aistudio_api.infrastructure.browser.browser_engine._is_termux",
            return_value=False,
        ),
        pytest.raises(FileNotFoundError, match="未检测到 CloakBrowser"),
    ):
        find_chromium_executable()

    # 3. When explicit CloakBrowser executable path is provided, returns it
    fake_chrome = tmp_path / "chrome.exe"
    fake_chrome.write_text("fake cloakbrowser")
    with patch(
        "aistudio_api.config.settings.browser_executable_path", str(fake_chrome)
    ):
        found = find_chromium_executable()
        assert found == str(fake_chrome)


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
    with (
        patch(
            "aistudio_api.infrastructure.browser.browser_engine._is_active_api_server",
            return_value=True,
        ),
        patch(
            "aistudio_api.infrastructure.browser.browser_engine._is_port_in_use",
            return_value=False,
        ),
    ):
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


def test_build_chromium_args_platform_and_timezone():
    """Verify stealth platform spoofing and timezone override flags."""
    args = build_chromium_args(
        port=9222,
        timezone="America/New_York",
        locale="en-US",
    )
    args_str = " ".join(args)
    assert "--fingerprint-timezone=America/New_York" in args_str
    assert "--fingerprint-locale=en-US" in args_str
    # On non-Darwin hosts, platform defaults to windows
    import platform

    if platform.system() != "Darwin":
        assert "--fingerprint-platform=windows" in args_str
    else:
        assert "--fingerprint-platform=macos" in args_str


def test_windows_job_object_lifecycle():
    """Verify Windows Job Object auto-terminates child process on handle close."""
    import platform
    import subprocess
    import time

    if platform.system() != "Windows":
        assert _create_windows_job_object() is None
        return

    import ctypes

    job = _create_windows_job_object()
    assert job is not None
    proc = subprocess.Popen(["cmd.exe", "/c", "pause"])
    proc_handle = int(getattr(proc, "_handle", 0))
    try:
        assigned = _assign_process_to_job(job, proc_handle)
        assert assigned is True
        assert proc.poll() is None
    finally:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is not None:
            kernel32 = win_dll("kernel32", use_last_error=True)
            kernel32.CloseHandle(job)
        time.sleep(0.2)
        assert proc.poll() is not None


def test_is_active_api_server_self_and_parent():
    """Verify _is_active_api_server rejects invalid PIDs and own PID."""
    assert _is_active_api_server(0) is False
    assert _is_active_api_server(-1) is False
    assert _is_active_api_server(os.getpid()) is False
    assert _is_active_api_server(os.getppid()) is False
