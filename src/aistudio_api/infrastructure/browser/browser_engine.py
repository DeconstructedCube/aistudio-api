"""Shared helpers for selecting and launching the browser backend.

Provides direct Chromium subprocess launching with stealth and performance flags,
as well as fallback launcher options for legacy backends.
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
from typing import Any

from aistudio_api.config import build_camoufox_proxy, settings

log = logging.getLogger("aistudio.browser")


def is_camoufox_engine() -> bool:
    return settings.browser_engine == "camoufox"


def describe_browser_backend() -> str:
    if is_camoufox_engine():
        return "camoufox"
    if settings.browser_channel:
        return f"chromium:{settings.browser_channel}"
    if settings.browser_executable_path:
        return f"chromium:{settings.browser_executable_path}"
    return "chromium"


def _derive_stable_fingerprint_seed(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return 10000 + (int(digest[:8], 16) % 90000)


def find_chromium_executable() -> str:
    """Find the best Chromium executable available on the system."""
    # 1. Configured explicit path
    if settings.browser_executable_path and os.path.exists(settings.browser_executable_path):
        return settings.browser_executable_path

    # 2. Cloakbrowser installed Chromium (~/.cloakbrowser/**/chrome)
    cloak_matches = sorted(glob.glob(os.path.expanduser("~/.cloakbrowser/**/chrome"), recursive=True))
    if cloak_matches:
        for match in reversed(cloak_matches):
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return match

    # 3. Playwright cached Chromium (~/.cache/ms-playwright/chromium-*/chrome-linux/chrome)
    pw_matches = sorted(
        glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"), recursive=True)
    )
    if pw_matches:
        for match in reversed(pw_matches):
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return match

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

    raise FileNotFoundError("Could not locate a valid Chromium executable on this system.")


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
        args.extend([
            "--headless=new",
            "--hide-scrollbars",
            "--window-size=1280,800",
        ])
    else:
        args.extend([
            "--start-maximized",
            "--ignore-gpu-blocklist",
        ])

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

    def __init__(self, process: subprocess.Popen[Any], port: int, user_data_dir: str | None = None):
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


def _build_cloakbrowser_args(
    *,
    headless: bool,
    stable_fingerprint_key: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Legacy helper for Playwright/cloakbrowser launcher."""
    fingerprint_seed = (
        _derive_stable_fingerprint_seed(stable_fingerprint_key)
        if stable_fingerprint_key
        else None
    )
    args: list[str] = [
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
        "--no-first-run",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-features=Translate,OptimizationHints,MediaRouter,DialLocalDiscovery,PreloadMediaEngagementData,CertificateTransparencyComponentUpdater,SitePerProcess,AudioServiceOutOfProcess",
    ]
    if not headless:
        args.append("--start-maximized")
        args.append("--ignore-gpu-blocklist")
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


def build_browser_launch_options(headless: bool | None = None) -> dict[str, Any]:
    is_headless = settings.browser_headless if headless is None else headless
    options: dict[str, Any] = {
        "headless": is_headless,
    }
    if not is_headless:
        options["args"] = ["--start-maximized"]
    proxy = build_camoufox_proxy(settings.proxy_url)
    if proxy:
        options["proxy"] = proxy
    if settings.browser_executable_path:
        options["executable_path"] = settings.browser_executable_path
    elif settings.browser_channel:
        options["channel"] = settings.browser_channel
    return options


def build_browser_context_options(headless: bool | None = None) -> dict[str, Any]:
    if is_camoufox_engine():
        return {}
    is_headless = settings.browser_headless if headless is None else headless
    if is_headless:
        return {}
    return {
        "no_viewport": True,
    }


def should_maximize_browser_window(headless: bool | None = None) -> bool:
    if is_camoufox_engine():
        return False
    return not (settings.browser_headless if headless is None else headless)


def sync_maximize_page_window(page: Any, *, headless: bool | None = None) -> None:
    if not should_maximize_browser_window(headless):
        return
    try:
        cdp = page.context.new_cdp_session(page)
        window = cdp.send("Browser.getWindowForTarget")
        cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {"windowState": "maximized"},
            },
        )
        page.wait_for_timeout(200)
    except Exception:
        pass


async def async_maximize_page_window(page: Any, *, headless: bool | None = None) -> None:
    if not should_maximize_browser_window(headless):
        return
    try:
        cdp = await page.context.new_cdp_session(page)
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {"windowState": "maximized"},
            },
        )
        await page.wait_for_timeout(200)
    except Exception:
        pass


def sync_launch_browser() -> tuple[Any, Any | None, Any | None]:
    if is_camoufox_engine():
        from camoufox.sync_api import Camoufox

        cf = Camoufox(
            headless=settings.browser_headless,
            main_world_eval=True,
            proxy=build_camoufox_proxy(settings.proxy_url),
        )
        browser = cf.__enter__()
        return browser, cf, None

    from cloakbrowser import launch

    headless = settings.browser_headless
    browser = launch(
        headless=headless,
        proxy=build_camoufox_proxy(settings.proxy_url),
        stealth_args=False,
        args=_build_cloakbrowser_args(headless=headless),
    )
    return browser, None, None


def sync_launch_persistent_context(
    user_data_dir: str,
    *,
    headless: bool | None = None,
    **context_kwargs: Any,
) -> Any:
    if is_camoufox_engine():
        raise RuntimeError("sync_launch_persistent_context() only supports Chromium backend")

    from cloakbrowser import launch_persistent_context

    if context_kwargs.pop("no_viewport", False):
        context_kwargs["viewport"] = None

    headless = settings.browser_headless if headless is None else headless
    return launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=headless,
        proxy=build_camoufox_proxy(settings.proxy_url),
        stealth_args=False,
        args=_build_cloakbrowser_args(
            headless=headless,
            stable_fingerprint_key=user_data_dir,
        ),
        **context_kwargs,
    )


async def async_launch_browser(*, headless: bool | None = None) -> Any:
    if is_camoufox_engine():
        raise RuntimeError("async_launch_browser() only supports Chromium backend")
    from cloakbrowser import launch_async

    headless = settings.browser_headless if headless is None else headless
    return await launch_async(
        headless=headless,
        proxy=build_camoufox_proxy(settings.proxy_url),
        stealth_args=False,
        args=_build_cloakbrowser_args(headless=headless),
    )
