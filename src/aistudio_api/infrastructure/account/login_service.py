"""Google 账号登录服务，通过有头浏览器完成登录并保存 cookie。"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
import termios
import time
from urllib.parse import urlencode
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from aistudio_api.infrastructure.browser.browser_engine import (
    async_maximize_page_window,
    build_browser_context_options,
    describe_browser_backend,
)
from aistudio_api.infrastructure.browser.camoufox_manager import CamoufoxManager

logger = logging.getLogger("aistudio.login")


class LoginStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class LoginSession:
    """登录会话状态。"""
    session_id: str
    status: LoginStatus = LoginStatus.PENDING
    account_id: str | None = None
    email: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class TerminalLoginStep:
    """终端辅助登录步骤。"""

    kind: str
    prompt: str
    sensitive: bool = False
    options: list[str] = field(default_factory=list)
    phase: str | None = None

    def signature(self) -> str:
        phase = self.phase or ""
        options = "|".join(self.options)
        return f"{phase}|{self.kind}|{options}"


class LoginService:
    """Google 账号登录服务。"""

    def __init__(self, port: int = 9223) -> None:
        self._port = port
        self._sessions: dict[str, LoginSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._terminal_lock = asyncio.Lock()

    def _generate_session_id(self) -> str:
        return f"login_{secrets.token_hex(8)}"

    async def start_login(
        self,
        account_store: Any,  # AccountStore
        name: str | None = None,
        *,
        headless: bool = False,
        ui_locale: str | None = None,
    ) -> str:
        """启动登录流程，返回 session_id。"""
        session_id = self._generate_session_id()
        session = LoginSession(session_id=session_id)
        self._sessions[session_id] = session
        # 启动后台任务
        task = asyncio.create_task(
            self._login_worker(
                session_id,
                account_store,
                name,
                headless=headless,
                ui_locale=ui_locale,
            )
        )
        self._tasks[session_id] = task
        return session_id

    def get_status(self, session_id: str) -> LoginSession | None:
        """获取登录状态。"""
        return self._sessions.get(session_id)

    def _terminal_available(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    async def _read_terminal_input(self, prompt: str, *, sensitive: bool = False) -> str:
        loop = asyncio.get_running_loop()
        fd = sys.stdin.fileno()
        future = loop.create_future()
        old_attrs = None

        def _on_input_ready() -> None:
            try:
                line = sys.stdin.readline()
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
                return
            if not future.done():
                future.set_result(line.rstrip("\r\n"))

        try:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            if sensitive and sys.stdin.isatty():
                old_attrs = termios.tcgetattr(fd)
                new_attrs = termios.tcgetattr(fd)
                new_attrs[3] &= ~termios.ECHO
                termios.tcsetattr(fd, termios.TCSADRAIN, new_attrs)
            loop.add_reader(fd, _on_input_ready)
            return await future
        except NotImplementedError:
            return await asyncio.to_thread(input, "")
        finally:
            try:
                loop.remove_reader(fd)
            except Exception:
                pass
            if old_attrs is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
                sys.stdout.write("\n")
                sys.stdout.flush()

    async def _find_visible_locator(self, page, selectors: list[str]):
        if not selectors:
            return None
        combined_selector = ", ".join(selectors)
        locator = page.locator(combined_selector).first
        try:
            await locator.wait_for(state="visible", timeout=3000)
            return locator
        except Exception:
            return None

    def _supports_switch_login_method(self, step: TerminalLoginStep) -> bool:
        return step.phase in {"totp", "ootp", "dp", "wa", "ipp", "bc", "kpe", "pwd", "challenge"} or step.kind in {"phone", "otp", "manual"}

    def _build_input_prompt(self, step: TerminalLoginStep) -> str:
        if self._supports_switch_login_method(step):
            return f"{step.prompt}（回车切换登录方式）"
        return step.prompt

    def _classify_google_login_phase(self, url: str) -> str | None:
        if not url or "accounts.google.com" not in url:
            return None
        if "/identifier" in url:
            return "identifier"
        if "/challenge/" in url or "/selectchallenge" in url:
            if "selection" in url or "selectchallenge" in url:
                return "selection"
            if "/pwd" in url or "/password" in url:
                return "pwd"
            if "/dp" in url:
                return "dp"
            if "/totp" in url:
                return "totp"
            if "/ootp" in url:
                return "ootp"
            if "/wa" in url or "/sk" in url:
                return "wa"
            if "/ipp" in url:
                return "ipp"
            if "/bc" in url:
                return "bc"
            if "/kpe" in url:
                return "kpe"
            return "challenge"
        return None

    def _step_from_phase(self, phase: str) -> TerminalLoginStep | None:
        mapping = {
            "identifier": TerminalLoginStep(kind="email", prompt="请输入邮箱", phase=phase),
            "pwd": TerminalLoginStep(kind="password", prompt="请输入密码", sensitive=True, phase=phase),
            "dp": TerminalLoginStep(kind="manual", prompt="请在手机上点击确认", phase=phase),
            "selection": TerminalLoginStep(kind="selection", prompt="请选择登录方式", phase=phase),
            "totp": TerminalLoginStep(kind="otp", prompt="请输入验证器验证码", phase=phase),
            "ootp": TerminalLoginStep(kind="otp", prompt="请输入安全码", phase=phase),
            "wa": TerminalLoginStep(kind="manual", prompt="请在设备上确认通行密钥（Passkey）或安全密钥", phase=phase),
            "ipp": TerminalLoginStep(kind="manual", prompt="请在设备上确认身份", phase=phase),
            "bc": TerminalLoginStep(kind="otp", prompt="请输入8位备用验证码", phase=phase),
            "kpe": TerminalLoginStep(kind="phone", prompt="请输入短信验证码", phase=phase),
            "challenge": TerminalLoginStep(kind="manual", prompt="请在浏览器中完成安全验证", phase=phase),
        }
        return mapping.get(phase)

    async def _detect_terminal_step(self, page) -> TerminalLoginStep | None:
        phase = self._classify_google_login_phase(page.url or "")
        payload = await page.evaluate(
            """
            (phase) => {
                const genericTitles = new Set([
                    "sign in",
                    "signin",
                    "google",
                    "welcome",
                    "使用 google 账号登录",
                    "登录",
                ]);
                const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== "hidden"
                        && style.display !== "none"
                        && rect.width > 0
                        && rect.height > 0;
                };
                const text = (el) => (el?.innerText || el?.textContent || "")
                    .replace(/\\s+/g, " ")
                    .trim();
                const cleanOptionText = (el) => {
                    return (el?.innerText || el?.textContent || "")
                        .replace(/[\\u203a\\u2192\\u276f>»›]/g, " ")
                        .replace(/\\s+/g, " ")
                        .trim();
                };
                const withTitle = (base, title) => {
                    const normalized = (title || "").trim().toLowerCase();
                    if (!normalized || genericTitles.has(normalized)) {
                        return base;
                    }
                    return `${base}（${title.trim()}）`;
                };
                const getCandidateElements = () => {
                    const candidateSelectors = [
                        "[data-challengetype]",
                        "[data-challengeindex]",
                        "[data-challenge-id]",
                        "li[role='link']",
                        "div[role='link']",
                        "li[role='button']",
                        "div[role='button']",
                        "ul > li",
                        "button",
                    ];
                    const ignored = new Set([
                        "back", "next", "try another way", "帮助", "隐私权", "条款",
                        "help", "privacy", "terms", "cancel", "取消", "返回", "下一步"
                    ]);
                    const seen = new Set();
                    const rawList = [];
                    for (const sel of candidateSelectors) {
                        const found = document.querySelectorAll(sel);
                        for (const el of found) {
                            if (visible(el) && !seen.has(el)) {
                                if (el.tagName.toLowerCase() === "ul") continue;
                                seen.add(el);
                                rawList.push(el);
                            }
                        }
                    }
                    const filtered = rawList.filter((el) => {
                        const val = cleanOptionText(el);
                        if (!val || val.length > 100 || ignored.has(val.toLowerCase())) {
                            return false;
                        }
                        const containsChild = rawList.some((other) => other !== el && el.contains(other));
                        if (containsChild) {
                            if (!el.hasAttribute("data-challengetype") && !el.getAttribute("role")) {
                                return false;
                            }
                        }
                        return true;
                    });
                    const unique = [];
                    const seenTexts = new Set();
                    for (const el of filtered) {
                        const val = cleanOptionText(el);
                        if (!seenTexts.has(val)) {
                            seenTexts.add(val);
                            unique.push(el);
                        }
                    }
                    return unique;
                };

                const bodyText = text(document.body).toLowerCase();
                const title = text(document.querySelector("h1, [role='heading']")) || document.title || "";
                const selectionOptions = getCandidateElements().map(cleanOptionText).slice(0, 8);

                if (phase === "selection" || (selectionOptions.length > 1 && (bodyText.includes("choose how you want to sign in") || bodyText.includes("选择登录方式") || bodyText.includes("verify it's you") || bodyText.includes("验证您的身份")))) {
                    return {
                        kind: "selection",
                        prompt: withTitle("请选择登录方式", title),
                        options: selectionOptions,
                    };
                }

                const chooser = Array.from(document.querySelectorAll("[data-identifier]"))
                    .filter(visible)
                    .map((el) => {
                        const email = el.getAttribute("data-identifier") || "";
                        const label = text(el) || email;
                        return label ? `${label}${email && !label.includes(email) ? ` (${email})` : ""}` : email;
                    })
                    .filter(Boolean)
                    .filter((item, index, arr) => arr.indexOf(item) === index)
                    .slice(0, 8);
                if (chooser.length > 0) {
                    return {
                        kind: "choose_account",
                        prompt: withTitle("请选择账号", title),
                        options: chooser,
                    };
                }

                const password = document.querySelector("input[type='password'], input[name='Passwd']");
                if (visible(password)) {
                    return {
                        kind: "password",
                        prompt: withTitle("请输入密码", title),
                        sensitive: true,
                    };
                }

                const otp = document.querySelector(
                    "input[autocomplete='one-time-code'], input[name='totpPin'], input[name='backupCode'], input[inputmode='numeric'], input[type='tel']"
                );
                if (visible(otp)) {
                    return {
                        kind: bodyText.includes("phone") ? "phone" : "otp",
                        prompt: withTitle(bodyText.includes("phone") ? "请输入短信验证码" : "请输入验证码", title),
                    };
                }

                const email = document.querySelector("input[type='email'], input[name='identifier']");
                if (visible(email)) {
                    if (bodyText.includes("recovery email")) {
                        return {
                            kind: "recovery_email",
                            prompt: withTitle("请输入恢复邮箱", title),
                        };
                    }
                    return {
                        kind: "email",
                        prompt: withTitle("请输入邮箱", title),
                    };
                }

                if (
                    bodyText.includes("tap yes") ||
                    bodyText.includes("check your phone") ||
                    bodyText.includes("security key") ||
                    bodyText.includes("passkey") ||
                    bodyText.includes("qr code")
                ) {
                    return {
                        kind: "manual",
                        prompt: withTitle("需要在设备上手动确认", title),
                    };
                }

                return null;
            }
            """,
            phase,
        )
        dom_step = None
        if payload:
            dom_step = TerminalLoginStep(
                kind=payload["kind"],
                prompt=payload.get("prompt") or "请输入",
                sensitive=bool(payload.get("sensitive")),
                options=list(payload.get("options") or []),
                phase=phase,
            )

        phase_step = self._step_from_phase(phase) if phase else None
        if phase_step is not None:
            if phase_step.kind == "selection":
                if dom_step is not None and dom_step.options:
                    dom_step.prompt = phase_step.prompt
                    dom_step.phase = phase
                    return dom_step
                return TerminalLoginStep(
                    kind="manual",
                    prompt="请选择登录方式（请在浏览器里点击）",
                    phase=phase,
                )
            if dom_step is not None and dom_step.kind == phase_step.kind:
                dom_step.phase = phase
                if phase_step.prompt:
                    dom_step.prompt = phase_step.prompt
                return dom_step
            return phase_step
        return dom_step

    async def _submit_terminal_value(self, page, step: TerminalLoginStep, value: str) -> bool:
        input_selectors = {
            "email": ["input[type='email']", "input[name='identifier']"],
            "password": ["input[type='password']", "input[name='Passwd']"],
            "recovery_email": ["input[type='email']"],
            "otp": [
                "input[autocomplete='one-time-code']",
                "input[name='totpPin']",
                "input[inputmode='numeric']",
                "input[type='tel']",
            ],
            "phone": ["input[type='tel']", "input[inputmode='numeric']"],
        }
        submit_selectors = {
            "email": [
                "#identifierNext button",
                "#identifierNext",
                "button:has-text('Next')",
                "button:has-text('下一步')",
                "div[role='button']:has-text('Next')",
                "div[role='button']:has-text('下一步')",
            ],
            "password": [
                "#passwordNext button",
                "#passwordNext",
                "button:has-text('Next')",
                "button:has-text('下一步')",
                "div[role='button']:has-text('Next')",
                "div[role='button']:has-text('下一步')",
            ],
            "recovery_email": [
                "button:has-text('Next')",
                "button:has-text('继续')",
                "button:has-text('下一步')",
                "div[role='button']:has-text('Next')",
                "div[role='button']:has-text('继续')",
                "div[role='button']:has-text('下一步')",
            ],
            "otp": [
                "button:has-text('Next')",
                "button:has-text('Verify')",
                "button:has-text('继续')",
                "button:has-text('下一步')",
                "div[role='button']:has-text('Next')",
                "div[role='button']:has-text('Verify')",
                "div[role='button']:has-text('继续')",
                "div[role='button']:has-text('下一步')",
            ],
            "phone": [
                "button:has-text('Next')",
                "button:has-text('继续')",
                "button:has-text('下一步')",
                "div[role='button']:has-text('Next')",
                "div[role='button']:has-text('继续')",
                "div[role='button']:has-text('下一步')",
            ],
        }

        locator = await self._find_visible_locator(page, input_selectors.get(step.kind, []))
        if locator is None:
            return False
        await locator.click()
        await locator.fill(value)
        submit = await self._find_visible_locator(page, submit_selectors.get(step.kind, []))
        if submit is not None:
            await submit.click()
        else:
            await locator.press("Enter")
        return True

    async def _choose_account_from_terminal(self, page, raw_value: str) -> bool:
        value = raw_value.strip()
        if not value:
            return False
        if not value.isdigit():
            return False

        target_index = int(value) - 1
        items = page.locator("[data-identifier]")
        visible_items = []
        for idx in range(await items.count()):
            candidate = items.nth(idx)
            try:
                if await candidate.is_visible():
                    visible_items.append(candidate)
            except Exception:
                continue

        if target_index < 0 or target_index >= len(visible_items):
            return False
        await visible_items[target_index].click()
        return True

    async def _choose_selection_from_terminal(self, page, raw_value: str) -> bool:
        value = raw_value.strip()
        if not value or not value.isdigit():
            return False

        target_index = int(value) - 1
        return bool(
            await page.evaluate(
                """
                (targetIndex) => {
                    const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== "hidden"
                            && style.display !== "none"
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const cleanOptionText = (el) => {
                        return (el?.innerText || el?.textContent || "")
                            .replace(/[\\u203a\\u2192\\u276f>»›]/g, " ")
                            .replace(/\\s+/g, " ")
                            .trim();
                    };
                    const candidateSelectors = [
                        "[data-challengetype]",
                        "[data-challengeindex]",
                        "[data-challenge-id]",
                        "li[role='link']",
                        "div[role='link']",
                        "li[role='button']",
                        "div[role='button']",
                        "ul > li",
                        "button",
                    ];
                    const ignored = new Set([
                        "back", "next", "try another way", "帮助", "隐私权", "条款",
                        "help", "privacy", "terms", "cancel", "取消", "返回", "下一步"
                    ]);
                    const seen = new Set();
                    const rawList = [];
                    for (const sel of candidateSelectors) {
                        const found = document.querySelectorAll(sel);
                        for (const el of found) {
                            if (visible(el) && !seen.has(el)) {
                                if (el.tagName.toLowerCase() === "ul") continue;
                                seen.add(el);
                                rawList.push(el);
                            }
                        }
                    }
                    const filtered = rawList.filter((el) => {
                        const val = cleanOptionText(el);
                        if (!val || val.length > 100 || ignored.has(val.toLowerCase())) {
                            return false;
                        }
                        const containsChild = rawList.some((other) => other !== el && el.contains(other));
                        if (containsChild) {
                            if (!el.hasAttribute("data-challengetype") && !el.getAttribute("role")) {
                                return false;
                            }
                        }
                        return true;
                    });
                    const unique = [];
                    const seenTexts = new Set();
                    for (const el of filtered) {
                        const val = cleanOptionText(el);
                        if (!seenTexts.has(val)) {
                            seenTexts.add(val);
                            unique.push(el);
                        }
                    }
                    if (targetIndex < 0 || targetIndex >= unique.length) {
                        return false;
                    }
                    const el = unique[targetIndex];
                    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
                    try {
                        el.click();
                    } catch (e) {}
                    try {
                        const evt = new MouseEvent("click", {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                        });
                        el.dispatchEvent(evt);
                    } catch (e) {}
                    return true;
                }
                """,
                target_index,
            )
        )

    async def _switch_login_method(self, page) -> bool:
        selectors = [
            "button:has-text('Try another way')",
            "button:has-text('try another way')",
            "button:has-text('Try another option')",
            "button:has-text('试试其他方式')",
            "button:has-text('尝试其他方式')",
            "button:has-text('其他方式')",
            "div[role='button']:has-text('Try another way')",
            "div[role='button']:has-text('try another way')",
            "div[role='button']:has-text('Try another option')",
            "div[role='button']:has-text('试试其他方式')",
            "div[role='button']:has-text('尝试其他方式')",
            "div[role='button']:has-text('其他方式')",
            "a:has-text('Try another way')",
            "a:has-text('try another way')",
            "a:has-text('试试其他方式')",
            "a:has-text('尝试其他方式')",
            "a:has-text('其他方式')",
        ]
        locator = await self._find_visible_locator(page, selectors)
        if locator is not None:
            await locator.click()
            return True

        return bool(
            await page.evaluate(
                """
                () => {
                    const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== "hidden"
                            && style.display !== "none"
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const text = (el) => (el?.innerText || el?.textContent || "")
                        .replace(/\\s+/g, " ")
                        .trim()
                        .toLowerCase();
                    const targets = [
                        "try another way",
                        "try another option",
                        "试试其他方式",
                        "尝试其他方式",
                        "其他方式",
                    ];
                    const nodes = Array.from(document.querySelectorAll("button, div[role='button'], a"));
                    const hit = nodes.find((el) => visible(el) && targets.some((target) => text(el).includes(target)));
                    if (!hit) {
                        return false;
                    }
                    hit.click();
                    return true;
                }
                """
            )
        )

    async def _wait_for_step_transition(
        self,
        page,
        previous_signature: str,
        *,
        timeout: float = 6.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            current_step = await self._detect_terminal_step(page)
            if current_step is None:
                return True
            current_signature = current_step.signature()
            if current_signature != previous_signature:
                return True
        return False

    async def _wait_for_manual_confirmation_or_input(
        self,
        session_id: str,
        page,
        signature: str,
        login_done: asyncio.Event,
        prompt: str,
    ) -> str:
        input_task = asyncio.create_task(self._read_terminal_input(prompt))
        transition_task = asyncio.create_task(
            self._wait_for_step_transition(page, signature, timeout=300.0)
        )
        login_done_task = asyncio.create_task(login_done.wait())
        done, pending = await asyncio.wait(
            {input_task, transition_task, login_done_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass

        if login_done_task in done and login_done.is_set():
            print(f"\n[{session_id}] 已收到确认，等待登录完成。")
            return "completed"

        if transition_task in done:
            try:
                progressed = transition_task.result()
            except Exception:
                progressed = False
            if progressed:
                print(f"[{session_id}] 已收到确认，正在继续。")
                return "progressed"
            return "stayed"

        raw_value = input_task.result()
        if raw_value.strip():
            print(f"[{session_id}] 这一步不用输入内容；直接回车可切换登录方式。")
            return "ignored"
        return "switch"

    def _build_login_url(self, *, ui_locale: str | None = None) -> str:
        query = {
            "continue": "https://aistudio.google.com",
        }
        if ui_locale:
            query["hl"] = ui_locale
        return f"https://accounts.google.com/ServiceLogin?{urlencode(query)}"

    async def _terminal_login_loop(
        self,
        session_id: str,
        page,
        login_done: asyncio.Event,
        *,
        headless: bool,
    ) -> None:
        if not self._terminal_available():
            logger.info("[%s] 当前不是交互终端，跳过终端辅助登录", session_id)
            return

        async with self._terminal_lock:
            print(f"\n[{session_id}] 终端登录助手已启用。")
            announced_signature: str | None = None
            last_prompt_signature: str | None = None
            last_prompt_at = 0.0

            while not login_done.is_set():
                try:
                    step = await self._detect_terminal_step(page)
                except Exception as exc:
                    logger.debug("[%s] 检测登录步骤失败: %s", session_id, exc)
                    await asyncio.sleep(1)
                    continue

                if step is None:
                    await asyncio.sleep(1)
                    continue

                signature = step.signature()
                if step.kind == "manual":
                    if self._supports_switch_login_method(step):
                        action = await self._wait_for_manual_confirmation_or_input(
                            session_id,
                            page,
                            signature,
                            login_done,
                            f"[{session_id}] {self._build_input_prompt(step)}: ",
                        )
                        if action == "completed":
                            return
                        if action == "progressed":
                            announced_signature = None
                            last_prompt_signature = None
                            last_prompt_at = 0.0
                            continue
                        last_prompt_signature = signature
                        last_prompt_at = time.monotonic()
                        if action == "ignored":
                            await asyncio.sleep(1)
                            continue
                        if action == "switch":
                            switched = await self._switch_login_method(page)
                            if switched:
                                announced_signature = None
                                last_prompt_signature = None
                                progressed = await self._wait_for_step_transition(page, signature)
                                if not progressed:
                                    last_prompt_signature = signature
                                    last_prompt_at = time.monotonic()
                                    print(f"[{session_id}] 还在当前登录方式，请按页面提示确认后再继续。")
                                continue
                            print(f"[{session_id}] 没找到“切换登录方式”入口，请按页面提示继续。")
                            await asyncio.sleep(1)
                            continue
                        print(f"[{session_id}] 页面还停留在当前步骤，请按页面提示确认后再继续。")
                        await asyncio.sleep(1)
                        continue
                    if signature != announced_signature:
                        print(f"[{session_id}] {step.prompt}。")
                        announced_signature = signature
                    await asyncio.sleep(3)
                    continue

                if step.kind in {"choose_account", "selection"}:
                    if signature != announced_signature:
                        label = "检测到账号选择页" if step.kind == "choose_account" else "检测到登录方式选择页"
                        print(f"[{session_id}] {label}：")
                        for idx, option in enumerate(step.options, start=1):
                            print(f"  {idx}. {option}")
                        announced_signature = signature
                    if signature == last_prompt_signature and time.monotonic() - last_prompt_at < 2.5:
                        await asyncio.sleep(0.8)
                        continue
                    prompt = "输入编号，留空表示改为手动操作"
                    raw_value = await self._read_terminal_input(f"[{session_id}] {prompt}: ")
                    last_prompt_signature = signature
                    last_prompt_at = time.monotonic()
                    if login_done.is_set():
                        return
                    if not raw_value.strip():
                        await asyncio.sleep(1)
                        continue
                    choose_ok = (
                        await self._choose_account_from_terminal(page, raw_value)
                        if step.kind == "choose_account"
                        else await self._choose_selection_from_terminal(page, raw_value)
                    )
                    if not choose_ok:
                        print(f"[{session_id}] 编号无效，重新输入。")
                        continue
                    announced_signature = None
                    last_prompt_signature = None
                    progressed = await self._wait_for_step_transition(page, signature)
                    if not progressed:
                        print(f"[{session_id}] 页面还停留在当前步骤，请按页面提示确认后再继续。")
                        last_prompt_signature = signature
                        last_prompt_at = time.monotonic()
                    continue

                if signature == last_prompt_signature and time.monotonic() - last_prompt_at < 2.5:
                    await asyncio.sleep(0.8)
                    continue
                raw_value = await self._read_terminal_input(
                    f"[{session_id}] {self._build_input_prompt(step)}: ",
                    sensitive=step.sensitive,
                )
                last_prompt_signature = signature
                last_prompt_at = time.monotonic()
                if login_done.is_set():
                    return
                if not raw_value.strip():
                    if self._supports_switch_login_method(step):
                        switched = await self._switch_login_method(page)
                        if switched:
                            announced_signature = None
                            last_prompt_signature = None
                            progressed = await self._wait_for_step_transition(page, signature)
                            if not progressed:
                                last_prompt_signature = signature
                                last_prompt_at = time.monotonic()
                                print(f"[{session_id}] 还在当前登录方式，请按页面提示确认后再继续。")
                            continue
                        print(f"[{session_id}] 没找到“切换登录方式”入口，请按页面提示继续。")
                        await asyncio.sleep(1)
                        continue
                    print(f"[{session_id}] 输入为空，这一步先跳过。")
                    await asyncio.sleep(1)
                    continue

                applied = await self._submit_terminal_value(page, step, raw_value.strip())
                if not applied:
                    print(f"[{session_id}] 没找到可填写的输入框，这一步请先在浏览器里手动处理。")
                    await asyncio.sleep(2)
                    continue

                announced_signature = None
                last_prompt_signature = None
                progressed = await self._wait_for_step_transition(page, signature)
                if not progressed:
                    last_prompt_signature = signature
                    last_prompt_at = time.monotonic()
                    print(f"[{session_id}] 页面还停留在当前步骤，请按页面提示确认后再继续。")

    async def _login_worker(
        self,
        session_id: str,
        account_store: Any,
        name: str | None,
        *,
        headless: bool,
        ui_locale: str | None,
    ) -> None:
        """登录工作协程。"""
        session = self._sessions[session_id]
        manager = CamoufoxManager(
            port=self._port,
            headless=headless,
        )
        playwright = None
        browser = None
        terminal_task: asyncio.Task | None = None
        try:
            # 启动浏览器
            logger.info("启动登录浏览器，端口 %d", self._port)
            await manager.start()
            logger.info("浏览器后端已准备: %s", describe_browser_backend())

            # 连接 Playwright
            from playwright.async_api import async_playwright
            playwright = await async_playwright().start()
            browser = await manager.launch_browser(playwright)
            context = await browser.new_context(**build_browser_context_options(headless=headless))
            page = await context.new_page()
            await async_maximize_page_window(page, headless=headless)

            # 设置登录完成检测
            login_done = asyncio.Event()
            login_aborted = asyncio.Event()
            detected_email: str | None = None

            def abort_login(reason: str) -> None:
                if login_done.is_set() or login_aborted.is_set():
                    return
                session.status = LoginStatus.FAILED
                session.error = reason
                login_aborted.set()
                logger.warning(reason)

            async def on_navigation(frame):
                nonlocal detected_email
                url = frame.url
                logger.debug("导航到: %s", url)
                # 检测登录完成：跳转到非登录页面
                if "accounts.google.com" not in url and "google.com" in url:
                    # 尝试提取邮箱
                    try:
                        detected_email = await page.evaluate("""
                            () => {
                                // 尝试从页面获取邮箱
                                const el = document.querySelector('[data-email]')
                                    || document.querySelector('.gb_nb')
                                    || document.querySelector('[aria-label*="@"]');
                                return el ? (el.getAttribute('data-email') || el.textContent.trim()) : null;
                            }
                        """)
                    except Exception:
                        pass
                    login_done.set()

            def on_page_close():
                abort_login("登录窗口已关闭")

            def on_context_close():
                abort_login("登录上下文已关闭")

            def on_browser_disconnected():
                abort_login("登录浏览器已断开连接")

            page.on("framenavigated", on_navigation)
            page.on("close", on_page_close)
            context.on("close", on_context_close)
            browser.on("disconnected", on_browser_disconnected)

            # 导航到 Google 登录页面
            logger.info("打开 Google 登录页面")
            await page.goto(
                self._build_login_url(ui_locale=ui_locale),
                wait_until="networkidle",
            )

            terminal_task = asyncio.create_task(
                self._terminal_login_loop(session_id, page, login_done, headless=headless)
            )

            # 等待用户完成登录（最多 5 分钟）
            logger.info("等待用户登录...")
            wait_tasks = [
                asyncio.create_task(login_done.wait()),
                asyncio.create_task(login_aborted.wait()),
            ]
            done, pending = await asyncio.wait(
                wait_tasks,
                timeout=300,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if not done:
                session.status = LoginStatus.FAILED
                session.error = "登录超时（5 分钟）"
                logger.warning("登录超时")
                return
            if login_aborted.is_set():
                return

            # 登录完成，保存 storage state
            logger.info("登录完成，保存 cookie")
            storage_state = await context.storage_state()

            # 尝试从 Google 账号页面获取邮箱
            if detected_email is None:
                try:
                    # 导航到 Google 账号页面
                    logger.info("尝试从 Google 账号页面获取邮箱")
                    await page.goto("https://myaccount.google.com", wait_until="networkidle")
                    await asyncio.sleep(2)  # 等待页面加载

                    # 从页面提取邮箱（优先匹配 *@gmail.com）
                    detected_email = await page.evaluate("""
                        () => {
                            const text = document.body.innerText;
                            // 直接匹配 *@gmail.com 邮箱
                            const gmailRegex = /[a-zA-Z0-9._%+-]+@gmail\\.com/g;
                            const matches = text.match(gmailRegex);
                            return matches ? matches[0] : null;
                        }
                    """)
                except Exception as e:
                    logger.warning("从 Google 账号页面获取邮箱失败: %s", e)

            # 如果还是没提取到邮箱，尝试从 storage state 的 origins 中提取
            if detected_email is None:
                try:
                    # 检查 localStorage 中是否有用户信息
                    for origin in storage_state.get("origins", []):
                        for item in origin.get("localStorage", []):
                            if "email" in item.get("name", "").lower():
                                detected_email = item.get("value")
                                break
                        if detected_email:
                            break
                except Exception:
                    pass

            # 保存账号
            account_name = name or detected_email or "Google 账号"
            if detected_email and not name:
                account_name = detected_email
            meta = account_store.save_account(
                name=account_name,
                email=detected_email,
                storage_state=storage_state,
            )

            session.status = LoginStatus.COMPLETED
            session.account_id = meta.id
            session.email = detected_email
            logger.info("账号已保存: %s (%s)", meta.id, detected_email)

        except Exception as e:
            session.status = LoginStatus.FAILED
            session.error = str(e)
            logger.exception("登录失败")
        finally:
            if terminal_task is not None:
                terminal_task.cancel()
                try:
                    await terminal_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            # 清理浏览器和 Playwright
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
            try:
                if playwright:
                    await playwright.stop()
            except Exception:
                pass
            try:
                await manager.stop()
            except Exception:
                pass
            # 清理任务引用
            self._tasks.pop(session_id, None)
