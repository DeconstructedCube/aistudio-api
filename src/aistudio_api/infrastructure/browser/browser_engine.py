"""Shared helpers for selecting and launching the Chromium browser backend.

Provides direct Chromium subprocess launching with stealth and performance flags.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import platform
import shutil
import signal
import subprocess
from pathlib import Path

from aistudio_api.config import settings

log = logging.getLogger("aistudio.browser")


def _derive_stable_fingerprint_seed(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return 10000 + (int(digest[:8], 16) % 90000)


def _is_termux() -> bool:
    """True iff running inside Termux (host or via app)."""
    return platform.system() == "Android" or os.environ.get("PREFIX", "").startswith(
        "/data/data/com.termux"
    )


def _find_wrapper_path() -> str | None:
    project_root = Path(__file__).resolve().parents[4]
    wrapper = project_root / "scripts" / "cloakbrowser_termux" / "run-chrome.sh"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        return str(wrapper)
    return None


def _resolve_local_chrome(match: str) -> str:
    """Route Termux chrome launches through the proot wrapper.

    On Termux the kernel loader cannot run glibc-built CloakBrowser, so the
    wrapper re-execs the binary inside ``proot-distro login <container>``.
    On every other host we return the binary path unchanged.
    """
    if _is_termux():
        wrapper = _find_wrapper_path()
        if wrapper:
            log.debug("Termux host: routing %s through wrapper %s", match, wrapper)
            return wrapper
    return match


def find_chromium_executable() -> str:
    """Find the best Chromium executable available on the system."""
    # 1. Configured explicit path
    if (
        settings.browser_executable_path
        and Path(settings.browser_executable_path).exists()
    ):
        return settings.browser_executable_path

    project_root = Path(__file__).resolve().parents[4]
    # 2. Local project-scoped CloakBrowser (.cloakbrowser/**/chrome)
    cloak_dir = project_root / ".cloakbrowser"
    if cloak_dir.is_dir():
        for pat in (
            "**/chrome",
            "**/Chromium.app/Contents/MacOS/Chromium",
            "**/chrome.exe",
        ):
            local_matches = sorted(cloak_dir.glob(pat))
            for match in reversed(local_matches):
                if match.is_file() and (
                    os.access(match, os.X_OK) or platform.system() == "Windows"
                ):
                    return _resolve_local_chrome(str(match))

    # 3. User-level CloakBrowser (~/.cloakbrowser/**/chrome)
    user_cloak_dir = Path.home() / ".cloakbrowser"
    if user_cloak_dir.is_dir():
        for pat in (
            "**/chrome",
            "**/chrome.exe",
            "**/Chromium.app/Contents/MacOS/Chromium",
        ):
            cloak_matches = sorted(user_cloak_dir.glob(pat))
            for match in reversed(cloak_matches):
                if match.is_file() and (
                    os.access(match, os.X_OK) or platform.system() == "Windows"
                ):
                    return _resolve_local_chrome(str(match))
    # 4. Standard binary names in PATH
    for name in (
        "google-chrome-stable",
        "google-chrome",
        "chromium-browser",
        "chromium",
        "chrome",
        "msedge",
        "brave-browser",
        "brave",
    ):
        p = shutil.which(name)
        if p and os.access(p, os.X_OK):
            return p

    # 5. Standard system installation paths across platforms
    sys_paths: list[str] = []
    sys_name = platform.system()
    if sys_name == "Darwin":
        sys_paths.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                str(
                    Path.home()
                    / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                ),
                str(Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )
    elif sys_name == "Windows":
        sys_paths.extend(
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(
                    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
                ),
                os.path.expandvars(
                    r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"
                ),
                os.path.expandvars(
                    r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
                ),
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                os.path.expandvars(
                    r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"
                ),
            ]
        )
    else:
        # Linux / Docker / Termux
        sys_paths.extend(
            [
                "/data/data/com.termux/files/usr/bin/chromium-browser",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/google-chrome",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/snap/bin/chromium",
                "/usr/local/bin/chrome",
                "/usr/local/bin/chromium",
            ]
        )

    for p in sys_paths:
        if Path(p).is_file() and (os.access(p, os.X_OK) or sys_name == "Windows"):
            return p

    raise FileNotFoundError(
        "Could not locate a valid Chromium executable on this system.\n"
        "Checked: CloakBrowser (.cloakbrowser / ~/.cloakbrowser), PATH, and standard system paths.\n"
        "Set AISTUDIO_BROWSER_EXECUTABLE environment variable to specify explicit path."
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
        "--js-flags=--max-old-space-size=128 --expose-gc",
        "--disk-cache-size=16777216",
        "--media-cache-size=1",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-backgrounding-occluded-windows",
        "--disable-ipc-flooding-protection",
        "--memory-pressure-off",
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
    if platform.system() == "Darwin":
        args.append("--fingerprint-platform=macos")
    elif _is_termux() or platform.system() == "Linux":
        args.append("--fingerprint-platform=linux")
    else:
        args.append("--fingerprint-platform=windows")

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
        """Terminate the Chromium subprocess tree safely across platforms."""
        if self.process.poll() is not None:
            return

        if platform.system() == "Windows":
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                        capture_output=True,
                    )
                except Exception:
                    self.process.kill()
            except Exception as e:
                log.debug("Error terminating Chromium process on Windows: %s", e)
            return

        # POSIX (Linux, macOS, Termux, Docker)
        try:
            pgid = os.getpgid(self.process.pid)
        except (ProcessLookupError, AttributeError):
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout_s)
            except Exception:
                pass
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
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
