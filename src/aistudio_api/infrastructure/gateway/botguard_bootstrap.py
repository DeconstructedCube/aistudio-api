"""BotGuard WAA bootstrap probe and base request template interception."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

from aistudio_api.domain.errors import SessionExpiredError
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    DIALOG_CLEANUP_JS,
    DOM_GC_CLEANUP_JS,
    INSTALL_HOOKS_JS,
    STOP_GENERATION_JS,
)
from aistudio_api.infrastructure.gateway.session_auth import is_login_page_url
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session.botguard")

BOTGUARD_BOOTSTRAP_PROMPT = "say '1'"


async def install_page_hooks(page: CDPPage) -> str:
    """Install WAA service hooks on page and extract snapshot function key."""
    initial_url = getattr(page, "url", "") or ""
    if is_login_page_url(initial_url):
        raise SessionExpiredError(
            f"Cookie 认证失效，当前页面为 Google 登录页: {initial_url}"
        )

    result = await page.evaluate(INSTALL_HOOKS_JS)
    if result == "already_hooked":
        snap_key = await page.evaluate("() => window.__AISTUDIO__?.snapKey || ''")
        if snap_key and isinstance(snap_key, str):
            return snap_key
        return "snapKey"
    if isinstance(result, str) and result.startswith("hooked:"):
        return result.split(":", 1)[1]

    for _ in range(3):
        await page.wait_for_timeout(1500)
        curr_url = getattr(page, "url", "") or ""
        if is_login_page_url(curr_url):
            raise SessionExpiredError(
                f"Cookie 认证失效，当前页面为 Google 登录页: {curr_url}"
            )
        result = await page.evaluate(INSTALL_HOOKS_JS)
        if result == "already_hooked":
            snap_key = await page.evaluate("() => window.__AISTUDIO__?.snapKey || ''")
            if snap_key and isinstance(snap_key, str):
                return snap_key
            return "snapKey"
        if isinstance(result, str) and result.startswith("hooked:"):
            return result.split(":", 1)[1]

    page_url = getattr(page, "url", "(unknown)")
    if is_login_page_url(str(page_url)):
        raise SessionExpiredError(
            f"Cookie 认证失效，当前页面为 Google 登录页: {page_url}"
        )
    page_title = await page.title()
    if (
        isinstance(page_title, str)
        and "sign in - google accounts" in page_title.lower()
    ):
        raise SessionExpiredError(
            f"Cookie 认证失效，当前页面为 Google 登录页: {page_url} (title={page_title!r})"
        )
    raise RuntimeError(
        f"Hook install failed: {result} (url={page_url}, title={page_title!r})"
    )


async def enable_temporary_chat(page: CDPPage) -> bool:
    """Enable Temporary Chat mode in AI Studio to prevent polluting user history."""
    try:
        return bool(
            await page.evaluate(
                """(() => {
                    const clickable = Array.from(document.querySelectorAll('button, [role="button"], mat-slide-toggle, a'));
                    const tempBtn = clickable.find(el => {
                        const s = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('mattooltip') || '')).toLowerCase();
                        return (s.includes('temporary chat') || s.includes('temporary mode') || s.includes('turn on temporary'))
                            && !s.includes('data use policy');
                    });
                    if (tempBtn) {
                        tempBtn.click();
                        return true;
                    }

                    const moreBtn = clickable.find(el => {
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const txt = (el.innerText || '').trim();
                        return aria === 'view more actions' || txt === 'more_vert';
                    });
                    if (moreBtn && moreBtn.getAttribute('aria-expanded') !== 'true') {
                        moreBtn.click();
                        const menuItem = Array.from(document.querySelectorAll('[role="menuitem"], .mat-mdc-menu-item')).find(item => {
                            const s = ((item.innerText || '') + ' ' + (item.getAttribute('aria-label') || '')).toLowerCase();
                            return s.includes('temporary');
                        });
                        if (menuItem) {
                            menuItem.click();
                            return true;
                        }
                        moreBtn.click();
                    }
                    return false;
                })()"""
            )
        )
    except Exception as e:
        log.debug("尝试激活 Temporary chat 异常: %s", e)
        return False


async def click_run_button(page: CDPPage) -> bool:
    """Find and trigger Run / Build button or dispatch Control+Enter shortcut."""
    clicked = await page.evaluate(
        """(() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const runBtn = buttons.find(b => {
                const t = (b.innerText || b.textContent || '').trim();
                return t === 'Run' || t.startsWith('Run\\n') || t.startsWith('Run Ctrl')
                    || t === 'Build' || t.startsWith('Build\\n') || t.startsWith('BuildCtrl')
                    || b.classList.contains('build-button')
                    || b.classList.contains('ctrl-enter-submits');
            });
            if (runBtn) {
                runBtn.click();
                return true;
            }
            return false;
        })()"""
    )
    if clicked:
        return True

    if await page.send_control_enter("textarea"):
        return True

    if await page.click("button.build-button"):
        return True
    if await page.click("button.ctrl-enter-submits"):
        return True
    if await page.click("button:has-text('Build')"):
        return True
    if await page.click("button:has-text('Run')"):
        return True
    return await page.click("button:has(mat-icon)")


async def bootstrap_botguard_and_template(
    page: CDPPage,
    reload_cookies_cb: Callable[[CDPPage], Awaitable[bool]] | None = None,
    is_proc_alive_cb: Callable[[], bool] | None = None,
    max_wait_seconds: int = 45,
) -> tuple[str, dict[str, object]]:
    """Execute 'say 1' bootstrap probe to capture BotGuardService and baseline wire template."""
    current_url = getattr(page, "url", "") or ""
    if "available-regions" in current_url:
        raise RuntimeError(
            "Google AI Studio 地区限制: 访问被重定向至 available-regions"
        )
    if is_login_page_url(current_url):
        raise SessionExpiredError(
            f"Cookie 认证失效，当前页面为 Google 登录页: {current_url}"
        )
    snap_key = await install_page_hooks(page)

    t0 = time.time()
    captured: dict[str, object] = {}
    original_text: str = ""

    def on_req(req: dict[str, object]) -> None:
        url = str(req.get("url") or "")
        if "GenerateContent" not in url or "Count" in url or captured:
            return
        body = str(req.get("post_data") or "")
        if not body:
            return
        captured["url"] = url
        captured["headers"] = req.get("headers") or {}
        captured["body"] = body

    unsub = page.on_request(on_req)
    await page.evaluate(DIALOG_CLEANUP_JS)
    await page.evaluate(
        """(() => {
            const clickable = Array.from(document.querySelectorAll('button, mat-card, a, [role="button"], [role="tab"], .nav-item'));
            const targets = ['Playground', 'Code and Chat', 'Chat prompt', 'New chat', 'Start building', 'Create prompt'];
            for (const term of targets) {
                const target = clickable.find(el => (el.innerText || el.textContent || '').includes(term));
                if (target) { target.click(); return; }
            }
        })()"""
    )
    await page.wait_for_timeout(800)

    try:
        try:
            await page.wait_for_selector("textarea", timeout_s=15.0)
        except Exception as err:
            dbg_url = getattr(page, "url", "") or ""
            if is_login_page_url(dbg_url):
                log.warning(
                    "等待输入框期间检测到登录页重定向: %s，尝试重新注入 Cookie...",
                    dbg_url,
                )
                if reload_cookies_cb and await reload_cookies_cb(page):
                    await page.wait_for_selector("textarea", timeout_s=15.0)
                else:
                    raise SessionExpiredError(
                        f"Cookie 认证失效，已被重定向到 Google 登录页: {dbg_url}"
                    ) from err
            if "available-regions" in dbg_url:
                raise RuntimeError(
                    "Google AI Studio 地区限制: 访问被重定向至 available-regions"
                ) from err

            dbg_title = await page.title()
            raise RuntimeError(
                f"textarea not found while capturing BotGuardService; url={dbg_url}, title={dbg_title}"
            ) from err

        original_text = str(
            (
                await page.evaluate(
                    "() => document.querySelector('textarea')?.value || ''"
                )
            )
            or ""
        )
        await enable_temporary_chat(page)
        await page.fill("textarea", BOTGUARD_BOOTSTRAP_PROMPT)
        await page.wait_for_timeout(600)
        await page.evaluate(DIALOG_CLEANUP_JS)

        if not await click_run_button(page):
            raise RuntimeError(
                "failed to trigger run button while capturing BotGuardService"
            )

        for i in range(max_wait_seconds):
            if is_proc_alive_cb and not is_proc_alive_cb():
                raise RuntimeError("Browser process died during BotGuard capture")
            if page.is_closed():
                raise RuntimeError("Browser page closed during BotGuard capture")

            curr_url = getattr(page, "url", "") or ""
            if is_login_page_url(curr_url):
                raise SessionExpiredError(
                    f"Cookie 认证失效，已跳转至登录页: {curr_url}"
                )

            await page.wait_for_timeout(1000)
            if await page.evaluate("() => !!window.__AISTUDIO__?.service"):
                if not captured:
                    with suppress(Exception):
                        in_page_tpl = await page.evaluate(
                            "() => window.__AISTUDIO__?.capturedTemplate || null"
                        )
                        if (
                            isinstance(in_page_tpl, dict)
                            and in_page_tpl.get("url")
                            and in_page_tpl.get("body")
                        ):
                            captured["url"] = str(in_page_tpl["url"])
                            captured["headers"] = in_page_tpl.get("headers") or {}
                            captured["body"] = str(in_page_tpl["body"])
                if not captured:
                    for _ in range(40):
                        if captured:
                            break
                        with suppress(Exception):
                            in_page_tpl = await page.evaluate(
                                "() => window.__AISTUDIO__?.capturedTemplate || null"
                            )
                            if (
                                isinstance(in_page_tpl, dict)
                                and in_page_tpl.get("url")
                                and in_page_tpl.get("body")
                            ):
                                captured["url"] = str(in_page_tpl["url"])
                                captured["headers"] = in_page_tpl.get("headers") or {}
                                captured["body"] = str(in_page_tpl["body"])
                                break
                        await page.wait_for_timeout(100)
                with suppress(Exception):
                    for _ in range(20):
                        snap_ready = await page.evaluate(
                            """async () => {
                                const a = window.__AISTUDIO__;
                                if (!a) return true;
                                if (typeof a.snapshot === 'string' && a.snapshot.length > 10) return true;
                                const dms = window.default_MakerSuite;
                                if (!dms || !a.snapKey || typeof dms[a.snapKey] !== 'function') return true;
                                try {
                                    const s = await dms[a.snapKey](
                                        a.service,
                                        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
                                    );
                                    if (typeof s === 'string' && s.length > 10) {
                                        a.snapshot = s;
                                        return true;
                                    }
                                } catch (e) {}
                                return false;
                            }"""
                        )
                        if snap_ready is not False:
                            break
                        await page.wait_for_timeout(400)
                with suppress(Exception):
                    await page.evaluate(STOP_GENERATION_JS)
                await page.fill("textarea", original_text)
                with suppress(Exception):
                    await page.evaluate(DOM_GC_CLEANUP_JS)

                if not captured:
                    raise RuntimeError(
                        "BotGuard service captured but wire template missing"
                    )

                log.info(
                    "BotGuard 握手成功并截获模板，耗时 %d 秒 (累计 %.1f 秒)",
                    i + 1,
                    time.time() - t0,
                )
                return snap_key, captured

        raise RuntimeError(f"BotGuardService capture timeout after {max_wait_seconds}s")
    finally:
        unsub()
        with suppress(Exception):
            await page.evaluate(
                "() => { if (window.__AISTUDIO__) window.__AISTUDIO__.interceptBootstrap = false; }"
            )
        with suppress(Exception):
            await page.fill("textarea", original_text)
