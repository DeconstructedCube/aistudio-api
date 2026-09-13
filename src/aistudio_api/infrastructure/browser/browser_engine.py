"""Shared helpers for selecting and launching the Chromium browser backend.

Provides direct Chromium subprocess launching with stealth and performance flags.
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

from aistudio_api.config import settings

log = logging.getLogger("aistudio.browser")


def _derive_stable_fingerprint_seed(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return 10000 + (int(digest[:8], 16) % 90000)


def _is_glibc_elf(path: str) -> bool:
    """True iff ``path`` is an ELF requiring a glibc dynamic linker.

    On Termux/Android the bionic loader cannot satisfy glibc-built binaries
    (``PT_INTERP`` points at ``/lib64/ld-linux-*.so.1``) and subsequent
    ``subprocess.Popen`` calls fail with ``FileNotFoundError: No such file or
    directory`` even though the file exists. Detect this so we route the
    launch through the project's glibc wrapper instead.

    Implementation note: we shell out to ``readelf -l`` rather than parsing
    the ELF header by hand. The project's bootstrap script (``bootstrap_*``)
    already depends on ``readelf`` for NEEDED resolution, and it is part of
    the standard Termux ``binutils`` package.
    """
    try:
        out = subprocess.check_output(
            ["readelf", "-l", path], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return "ld-linux" in out


def _requires_glibc_interpreter() -> bool:
    """True on hosts whose kernel loader cannot exec glibc ELFs directly."""
    if platform.system() == "Android":
        return True
    prefix = os.environ.get("PREFIX", "")
    return prefix.startswith("/data/data/com.termux")


def _find_wrapper_path() -> str | None:
    project_root = Path(__file__).resolve().parents[4]
    wrapper = project_root / "scripts" / "cloakbrowser_termux" / "run-chrome.sh"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        return str(wrapper)
    return None


def _resolve_local_chrome(match: str) -> str:
    """Return the launch path for a locally-discovered chrome binary.

    On hosts where the kernel loader cannot run glibc ELFs (Termux / Android),
    route the launch through the Termux glibc wrapper so the binary is exec'd
    by the project's glibc dynamic linker instead of bionic. Anywhere else,
    return the chrome path unchanged.
    """
    wrapper_path = _find_wrapper_path()
    if wrapper_path and _requires_glibc_interpreter() and _is_glibc_elf(match):
        log.debug(
            "glibc ELF %s -> routing through wrapper %s", match, wrapper_path
        )
        return wrapper_path
    return match


def find_chromium_executable() -> str:
    """Find the best Chromium executable available on the system."""
    # 1. Configured explicit path
    if settings.browser_executable_path and os.path.exists(
        settings.browser_executable_path
    ):
        return settings.browser_executable_path

    project_root = Path(__file__).resolve().parents[4]
    # 3. Local project-scoped CloakBrowser (.cloakbrowser/**/chrome)
    local_cloak_patterns = [
        str(project_root / ".cloakbrowser" / "**" / "chrome"),
        str(project_root / ".cloakbrowser" / "**" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"),
        str(project_root / ".cloakbrowser" / "**" / "chrome.exe"),
    ]
    for pat in local_cloak_patterns:
        local_matches = sorted(glob.glob(pat, recursive=True))
        if local_matches:
            for match in reversed(local_matches):
                if os.path.isfile(match) and (os.access(match, os.X_OK) or platform.system() == "Windows"):
                    return _resolve_local_chrome(match)

    # 3. User-level CloakBrowser (~/.cloakbrowser/**/chrome)
    user_cloak_patterns = [
        os.path.expanduser("~/.cloakbrowser/**/chrome"),
        os.path.expanduser("~/.cloakbrowser/**/chrome.exe"),
        os.path.expanduser("~/.cloakbrowser/**/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for pat in user_cloak_patterns:
        cloak_matches = sorted(glob.glob(pat, recursive=True))
        if cloak_matches:
            for match in reversed(cloak_matches):
                if os.path.isfile(match) and (os.access(match, os.X_OK) or platform.system() == "Windows"):
                    return _resolve_local_chrome(match)
    # 3. Playwright cached Chromium (~/.cache/ms-playwright/chromium-*/chrome-linux/chrome)
    pw_matches = sorted(
        glob.glob(
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
            recursive=True,
        )
    )
    if pw_matches:
        for match in reversed(pw_matches):
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return _resolve_local_chrome(match)

    # 4. Standard binary names in PATH
    for name in ("chromium-browser", "chromium", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p and os.access(p, os.X_OK):
            return p

    # 5. Termux / Linux standard system paths
    for p in (
        "/data/data/com.termux/files/usr/bin/chromium-browser",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    # 6. Repo-committed launcher wrapper (Termux glibc + CloakBrowser).
    # Last-resort fallback: when the only Chromium available is webgl-less
    # (e.g. Termux's native build), this wrapper resolves the project's
    # CloakBrowser with the correct glibc loader. Beep & fast.
    wrapper_path = _find_wrapper_path()
    if wrapper_path:
        return wrapper_path

    raise FileNotFoundError(
        "Could not locate a valid Chromium executable on this system.\n"
        "Hint: run `python3 scripts/bootstrap_cloakbrowser_termux.py` to provision\n"
        "the project's bundled CloakBrowser (anti-bot Chromium with WebGL/SwiftShader)."
    )


def build_chromium_args(
    port: int,
    user_data_dir: str | None = None,
    *,
    headless: bool | None = None,
    stable_fingerprint_key: str | None = None,
    proxy_url: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build optimized mobile/stealth CLI arguments for direct Chromium launch."""
    is_headless = settings.browser_headless if headless is None else headless
    args: list[str] = [
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--in-process-gpu",
        "--disable-software-rasterizer",
        "--renderer-process-limit=1",
        "--no-zygote",
        "--disable-breakpad",
        "--mute-audio",
        "--disable-audio",
        "--disable-site-isolation-trials",
        "--js-flags=--max-old-space-size=128",
        "--disk-cache-size=16777216",
        "--media-cache-size=1",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-speech-api",
        "--disable-component-update",
        "--disable-default-apps",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-features=Translate,OptimizationHints,MediaRouter,DialLocalDiscovery,PreloadMediaEngagementData,CertificateTransparencyComponentUpdater,SitePerProcess,AudioServiceOutOfProcess",
    ]

    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")

    if is_headless:
        args.extend(
            [
                "--headless=new",
                "--hide-scrollbars",
                "--window-size=1280,800",
            ]
        )
    else:
        args.extend(
            [
                "--start-maximized",
                "--ignore-gpu-blocklist",
            ]
        )

    effective_proxy = proxy_url or settings.proxy_url
    if effective_proxy:
        args.append(f"--proxy-server={effective_proxy}")

    fingerprint_seed = (
        _derive_stable_fingerprint_seed(stable_fingerprint_key)
        if stable_fingerprint_key
        else None
    )
    if fingerprint_seed is not None:
        args.append(f"--fingerprint={fingerprint_seed}")
    args.append(
        "--fingerprint-platform=macos"
        if platform.system() == "Darwin"
        else "--fingerprint-platform=windows"
    )

    if extra_args:
        args.extend(extra_args)
    return args


class ChromiumProcess:
    """Manages lifecycle of a direct Chromium subprocess."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        port: int,
        user_data_dir: str | None = None,
    ):
        self.process = process
        self.port = port
        self.user_data_dir = user_data_dir

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def terminate(self, timeout_s: float = 3.0) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
        except Exception as e:
            log.debug("Error terminating Chromium process: %s", e)


def launch_chromium_process(
    port: int,
    user_data_dir: str | None = None,
    *,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
) -> ChromiumProcess:
    """Launch Chromium binary directly as a subprocess."""
    executable = find_chromium_executable()
    if user_data_dir:
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    args = build_chromium_args(
        port=port,
        user_data_dir=user_data_dir,
        headless=headless,
        stable_fingerprint_key=user_data_dir,
        extra_args=extra_args,
    )
    cmd = [executable, *args]
    log.info("Launching Chromium binary %s on port %d", executable, port)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return ChromiumProcess(proc, port, user_data_dir)
