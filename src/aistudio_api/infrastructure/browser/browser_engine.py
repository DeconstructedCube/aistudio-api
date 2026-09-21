"""Shared helpers for selecting and launching the Chromium browser backend.

Provides direct Chromium subprocess launching with stealth and performance flags.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
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
        "--js-flags=--max-old-space-size=128 --expose-gc --optimize-for-size",
        "--disk-cache-size=16777216",
        "--media-cache-size=1",
        "--enable-low-end-device-mode",
        "--aggressive-cache-discard",
        "--disable-smooth-scrolling",
        "--disable-threaded-animation",
        "--disable-threaded-scrolling",
        "--disable-backing-store-limit",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-backgrounding-occluded-windows",
        "--disable-ipc-flooding-protection",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-speech-api",
        "--disable-component-update",
        "--disable-default-apps",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-features=Translate,OptimizationHints,MediaRouter,DialLocalDiscovery,PreloadMediaEngagementData,CertificateTransparencyComponentUpdater,SitePerProcess,AudioServiceOutOfProcess,CalculateNativeWinOcclusion,InterestFeedContentSuggestions,BackForwardCache",
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


def _setup_child_pdeathsig() -> None:
    """Configure Linux kernel to send SIGKILL to this child process if the parent dies."""
    if platform.system() in ("Linux", "Android") or _is_termux():
        try:
            import ctypes

            libc = ctypes.CDLL(None)
            # PR_SET_PDEATHSIG = 1
            libc.prctl(1, signal.SIGKILL, 0, 0, 0)
        except Exception:
            pass


def _is_active_api_server(pid: int) -> bool:
    """Check if a process is a live running AI Studio API server instance."""
    if pid <= 1:
        return False
    if pid in (os.getpid(), os.getppid()):
        return False
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            cmd = (
                cmdline_path.read_bytes()
                .decode("utf-8", errors="ignore")
                .replace("\x00", " ")
            )
            if any(
                k in cmd
                for k in ("main.py server", "aistudio-api-server", "uvicorn")
            ):
                return True
    except Exception:
        pass
    return False


def _is_port_in_use(port: int) -> bool:
    """Check if a TCP port on localhost is currently listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def cleanup_stale_chromium(
    port: int,
    user_data_dir: str | None = None,
    *,
    timeout_s: float = 3.0,
) -> list[int]:
    """Scan and terminate stale or orphaned Chromium / CloakBrowser processes.

    SAFETY GUARANTEE:
      Never terminates browser processes owned by a live active AI Studio API server.
      Only cleans up:
        1. True orphan processes (PPid == 1 or parent dead).
        2. Stale processes matching port/data-dir left behind from past crashes.
    Also cleans up stale lock files in user_data_dir and waits for port release.
    """
    cur_pid = os.getpid()
    parent_pid = os.getppid()
    candidate_pids: set[int] = set()

    proc_dir = Path("/proc")
    if proc_dir.is_dir():
        for proc_path in proc_dir.glob("[0-9]*"):
            try:
                pid = int(proc_path.name)
            except ValueError:
                continue
            if pid in (cur_pid, parent_pid):
                continue

            # Safety check: if parent is a live active API server, NEVER touch it!
            ppid = 0
            try:
                with Path(f"/proc/{pid}/status").open(encoding="utf-8") as sf:
                    for line in sf:
                        if line.startswith("PPid:"):
                            ppid = int(line.split(":", 1)[1].strip())
                            break
            except Exception:
                pass

            if ppid > 1 and _is_active_api_server(ppid):
                continue

            try:
                with Path(f"/proc/{pid}/cmdline").open("rb") as cf:
                    cmd = (
                        cf.read()
                        .decode("utf-8", errors="ignore")
                        .replace("\x00", " ")
                    )
                match_port = f"--remote-debugging-port={port}" in cmd
                match_udd = bool(
                    user_data_dir
                    and user_data_dir in cmd
                    and ("chrome" in cmd or "proot" in cmd)
                )
                match_orphan_proot = bool(
                    "proot" in cmd
                    and ("cloakbrowser" in cmd or "chrome" in cmd)
                    and ppid == 1
                )
                if match_port or match_udd or match_orphan_proot:
                    candidate_pids.add(pid)
            except Exception:
                pass
    else:
        # Non-/proc POSIX fallback (e.g. macOS)
        if platform.system() != "Windows":
            try:
                res = subprocess.run(
                    ["ps", "-eo", "pid,ppid,args"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in res.stdout.splitlines()[1:]:
                    parts = line.strip().split(None, 2)
                    if len(parts) < 3:
                        continue
                    try:
                        pid = int(parts[0])
                        ppid = int(parts[1])
                        cmd = parts[2]
                    except ValueError:
                        continue
                    if pid in (cur_pid, parent_pid):
                        continue
                    if ppid > 1 and _is_active_api_server(ppid):
                        continue
                    if (
                        f"--remote-debugging-port={port}" in cmd
                        or (user_data_dir and user_data_dir in cmd and "chrome" in cmd)
                        or ("chrome" in cmd and ppid == 1)
                    ):
                        candidate_pids.add(pid)
            except Exception as e:
                log.debug("Fallback process scan failed: %s", e)

    killed_pids: list[int] = []
    if candidate_pids:
        log.info(
            "Found %d stale/orphan browser process(es) to clean up: %s",
            len(candidate_pids),
            sorted(candidate_pids),
        )
        # 1. Send SIGTERM to process groups or pids
        for pid in sorted(candidate_pids):
            try:
                pgid = os.getpgid(pid) if hasattr(os, "getpgid") else None
                if pgid is not None and pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as e:
                log.debug("SIGTERM failed for PID %d: %s", pid, e)

        # 2. Wait up to 1.0s for graceful shutdown
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if Path("/proc").is_dir():
                alive = [p for p in candidate_pids if Path(f"/proc/{p}").exists()]
            else:
                alive = []
            if not alive:
                break
            time.sleep(0.1)

        # 3. Force SIGKILL any remaining candidates
        for pid in sorted(candidate_pids):
            try:
                pgid = os.getpgid(pid) if hasattr(os, "getpgid") else None
                if pgid is not None and pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
                killed_pids.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as e:
                log.debug("SIGKILL failed for PID %d: %s", pid, e)

    # 4. Clean up stale Chromium lock files in user_data_dir
    if user_data_dir:
        udd_path = Path(user_data_dir)
        if udd_path.is_dir():
            for lock_name in (
                "SingletonLock",
                "SingletonCookie",
                "SingletonSocket",
                "DevToolsActivePort",
            ):
                lock_file = udd_path / lock_name
                if lock_file.is_symlink() or lock_file.exists():
                    try:
                        lock_file.unlink()
                        log.debug("Removed stale lock file: %s", lock_file)
                    except Exception as e:
                        log.debug("Could not remove lock %s: %s", lock_file, e)

    # 5. Wait until port is verified free
    if _is_port_in_use(port):
        port_deadline = time.time() + timeout_s
        while time.time() < port_deadline and _is_port_in_use(port):
            time.sleep(0.1)

    return killed_pids


def _spawn_process_watchdog(
    target_pid: int, target_pgid: int | None
) -> tuple[subprocess.Popen[bytes] | None, int | None]:
    """Spawn a lightweight companion process that monitors parent process via pipe EOF.

    When this Python parent process dies (even via SIGKILL or OOM), the kernel automatically
    closes the pipe write-end. The companion process unblocks on EOF and sends SIGKILL
    to the entire browser process group, leaving zero orphans.
    """
    if platform.system() == "Windows":
        return None, None

    try:
        pipe_r, pipe_w = os.pipe()
        watcher_code = (
            "import os, sys, signal, time\n"
            "try:\n"
            "    r = int(sys.argv[1])\n"
            "    t_pid = int(sys.argv[2])\n"
            "    t_pgid = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != 'None' else None\n"
            "    os.read(r, 1)\n"
            "except Exception:\n"
            "    pass\n"
            "if t_pgid is not None:\n"
            "    try:\n"
            "        os.killpg(t_pgid, signal.SIGTERM)\n"
            "        time.sleep(0.2)\n"
            "        os.killpg(t_pgid, signal.SIGKILL)\n"
            "    except Exception:\n"
            "        pass\n"
            "try:\n"
            "    os.kill(t_pid, signal.SIGKILL)\n"
            "except Exception:\n"
            "    pass\n"
        )
        cmd = [
            sys.executable,
            "-c",
            watcher_code,
            str(pipe_r),
            str(target_pid),
            str(target_pgid) if target_pgid is not None else "None",
        ]
        watcher = subprocess.Popen(
            cmd,
            pass_fds=[pipe_r],
            preexec_fn=_setup_child_pdeathsig,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        os.close(pipe_r)
        return watcher, pipe_w
    except Exception as e:
        log.debug("Could not spawn watchdog companion: %s", e)
        return None, None


_ACTIVE_CHROMIUM_PROCESSES: set[ChromiumProcess] = set()


def _cleanup_all_chromium() -> None:
    """Terminate all active Chromium instances managed by this process."""
    for proc in list(_ACTIVE_CHROMIUM_PROCESSES):
        with contextlib.suppress(Exception):
            proc.terminate()


_cleanup_handlers_installed: bool = False


def install_process_cleanup_handlers() -> None:
    """Register atexit and POSIX signal handlers to guarantee browser termination on process exit."""
    global _cleanup_handlers_installed
    if _cleanup_handlers_installed:
        return
    _cleanup_handlers_installed = True

    atexit.register(_cleanup_all_chromium)

    if platform.system() != "Windows":
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                prev_handler = signal.getsignal(sig)

                def _make_handler(s: signal.Signals, prev: object):
                    def _handler(signum: int, frame: object) -> None:
                        _cleanup_all_chromium()
                        if callable(prev) and prev not in (
                            signal.SIG_IGN,
                            signal.SIG_DFL,
                        ):
                            prev(signum, frame)
                        elif signum == signal.SIGINT:
                            raise KeyboardInterrupt
                        else:
                            sys.exit(128 + signum)

                    return _handler

                signal.signal(sig, _make_handler(sig, prev_handler))
            except (ValueError, AttributeError, TypeError):
                pass


class ChromiumProcess:
    """Manages lifecycle of a direct Chromium subprocess with companion watchdog."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        port: int,
        user_data_dir: str | None = None,
        *,
        watcher: subprocess.Popen[bytes] | None = None,
        pipe_w: int | None = None,
        pgid: int | None = None,
    ):
        self.process = process
        self.port = port
        self.user_data_dir = user_data_dir
        self.watcher = watcher
        self.pipe_w = pipe_w
        try:
            self.pgid = pgid or (
                os.getpgid(process.pid) if hasattr(os, "getpgid") else None
            )
        except Exception:
            self.pgid = None
        _ACTIVE_CHROMIUM_PROCESSES.add(self)

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def terminate(self, timeout_s: float = 3.0) -> None:
        """Terminate the Chromium subprocess tree and companion watchdog safely."""
        _ACTIVE_CHROMIUM_PROCESSES.discard(self)

        # Close the write end of the pipe so the companion watchdog knows to exit cleanly
        if self.pipe_w is not None:
            with contextlib.suppress(Exception):
                os.close(self.pipe_w)
            self.pipe_w = None

        if self.watcher is not None and self.watcher.poll() is None:
            try:
                self.watcher.terminate()
                self.watcher.wait(timeout=1.0)
            except Exception:
                with contextlib.suppress(Exception):
                    self.watcher.kill()
            self.watcher = None

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
        pgid = self.pgid
        if pgid is None:
            try:
                pgid = os.getpgid(self.process.pid)
            except (ProcessLookupError, AttributeError):
                pgid = None

        if pgid is not None and pgid != os.getpgrp():
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGTERM)
        else:
            with contextlib.suppress(Exception):
                self.process.terminate()

        try:
            self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            if pgid is not None and pgid != os.getpgrp():
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(pgid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                self.process.kill()
            with contextlib.suppress(Exception):
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
    """Launch Chromium binary directly as a subprocess with watchdog protection."""
    install_process_cleanup_handlers()

    # Clean up any stale or orphaned browser processes on port before launching
    cleanup_stale_chromium(port=port, user_data_dir=user_data_dir)

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

    preexec = _setup_child_pdeathsig if platform.system() != "Windows" else None
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=preexec,
    )

    pgid = None
    with contextlib.suppress(Exception):
        pgid = os.getpgid(proc.pid)

    watcher, pipe_w = _spawn_process_watchdog(target_pid=proc.pid, target_pgid=pgid)
    return ChromiumProcess(
        proc,
        port,
        user_data_dir,
        watcher=watcher,
        pipe_w=pipe_w,
        pgid=pgid,
    )
