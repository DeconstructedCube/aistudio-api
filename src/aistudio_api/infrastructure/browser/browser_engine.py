"""Shared helpers for selecting and launching the browser backend."""

from __future__ import annotations

import hashlib
import platform
from typing import Any

from aistudio_api.config import build_camoufox_proxy, settings


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


def _build_cloakbrowser_args(
    *,
    headless: bool,
    stable_fingerprint_key: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build cloakbrowser args without `--no-sandbox`.

    cloakbrowser's default stealth args currently inject `--no-sandbox` and a
    random fingerprint seed. Both are undesirable for our long-lived profiles:
    `--no-sandbox` is easy to spot, and random seeds make a persistent profile
    present a different browser fingerprint on each launch.
    """
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
    """Launch a sync browser session.

    Returns:
        tuple of (browser, camoufox_context_manager, playwright_instance)
    """
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
    """Launch a persistent Chromium BrowserContext backed by a profile dir."""
    if is_camoufox_engine():
        raise RuntimeError("sync_launch_persistent_context() only supports Chromium backend")

    from cloakbrowser import launch_persistent_context

    # cloakbrowser's persistent launcher treats `viewport=None` as "use the real
    # window size", while passing `no_viewport=True` through kwargs can leave it
    # with conflicting viewport settings. Normalize it here so non-headless UI
    # keeps the same auto-fit behavior as the old browser.new_context path.
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
    """Launch an async browser session via cloakbrowser."""
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
