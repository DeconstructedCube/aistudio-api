import asyncio
import sys
import types

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
playwright_module = sys.modules.setdefault("playwright", types.ModuleType("playwright"))
async_api_module = sys.modules.setdefault("playwright.async_api", types.ModuleType("playwright.async_api"))
setattr(playwright_module, "async_api", async_api_module)
setattr(async_api_module, "async_playwright", lambda: None)

from aistudio_api.infrastructure.account import login_service as login_module
from aistudio_api.infrastructure.account.login_service import LoginService, LoginSession, LoginStatus


class FakePage:
    def __init__(self, *, close_on_goto: bool = False) -> None:
        self.url = ""
        self._handlers: dict[str, list] = {}
        self._close_on_goto = close_on_goto

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.url = url
        if self._close_on_goto:
            await self.emit("close")

    async def evaluate(self, script: str):
        return None

    async def emit(self, event: str, *args) -> None:
        for callback in self._handlers.get(event, []):
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self._handlers: dict[str, list] = {}

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    async def new_page(self) -> FakePage:
        return self._page

    async def storage_state(self) -> dict:
        return {"cookies": [], "origins": []}


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self._handlers: dict[str, list] = {}

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    async def new_context(self, **kwargs) -> FakeContext:
        return self._context

    async def close(self) -> None:
        await self.emit("disconnected")

    async def emit(self, event: str, *args) -> None:
        for callback in self._handlers.get(event, []):
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result


class FakeManager:
    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser

    async def start(self) -> None:
        return None

    async def launch_browser(self, playwright) -> FakeBrowser:
        return self._browser

    async def stop(self) -> None:
        return None


class FakePlaywright:
    async def stop(self) -> None:
        return None


class FakeAsyncPlaywrightStarter:
    async def start(self) -> FakePlaywright:
        return FakePlaywright()


class FakeAccountStore:
    def __init__(self) -> None:
        self.saved = False

    def save_account(self, **kwargs):
        self.saved = True
        raise AssertionError("closed login flow should not save account")


def test_login_session_fails_immediately_when_browser_window_is_closed(monkeypatch):
    page = FakePage(close_on_goto=True)
    context = FakeContext(page)
    browser = FakeBrowser(context)
    manager = FakeManager(browser)
    store = FakeAccountStore()

    monkeypatch.setattr(login_module, "CamoufoxManager", lambda port, headless: manager)
    monkeypatch.setattr(login_module, "describe_browser_backend", lambda: "camoufox")
    monkeypatch.setattr(login_module, "build_browser_context_options", lambda headless=None: {})

    async def fake_maximize_page_window(page, *, headless):
        return None

    async def fake_terminal_login_loop(self, session_id, page, login_done, *, headless):
        await asyncio.sleep(3600)

    monkeypatch.setattr(login_module, "async_maximize_page_window", fake_maximize_page_window)
    monkeypatch.setattr(LoginService, "_terminal_login_loop", fake_terminal_login_loop)
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: FakeAsyncPlaywrightStarter())

    service = LoginService()
    session = LoginSession(session_id="login_test")
    service._sessions[session.session_id] = session

    asyncio.run(
        asyncio.wait_for(
            service._login_worker(
                session.session_id,
                store,
                None,
                headless=False,
                ui_locale=None,
            ),
            timeout=1,
        )
    )

    assert session.status == LoginStatus.FAILED
    assert session.error == "登录窗口已关闭"
    assert store.saved is False

def test_classify_google_login_phase():
    service = LoginService()

    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/identifier?continue=...") == "identifier"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/pwd?TL=...") == "pwd"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/selection?TL=...") == "selection"
    assert service._classify_google_login_phase("https://accounts.google.com/signin/challenge/selectchallenge?TL=...") == "selection"
    assert service._classify_google_login_phase("https://accounts.google.com/v2/challenge/dp?TL=...") == "dp"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/totp?TL=...") == "totp"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/ootp?TL=...") == "ootp"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/wa?TL=...") == "wa"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/ipp?TL=...") == "ipp"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/bc?TL=...") == "bc"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/kpe?TL=...") == "kpe"
    assert service._classify_google_login_phase("https://accounts.google.com/v3/signin/challenge/unknown?TL=...") == "challenge"
    assert service._classify_google_login_phase("https://example.com/login") is None
    assert service._classify_google_login_phase("") is None


def test_step_from_phase():
    service = LoginService()

    ident = service._step_from_phase("identifier")
    assert ident.kind == "email"
    assert ident.phase == "identifier"

    pwd = service._step_from_phase("pwd")
    assert pwd.kind == "password"
    assert pwd.sensitive is True

    selection = service._step_from_phase("selection")
    assert selection.kind == "selection"

    wa = service._step_from_phase("wa")
    assert wa.kind == "manual"

    bc = service._step_from_phase("bc")
    assert bc.kind == "otp"


def test_supports_switch_login_method():
    service = LoginService()

    assert service._supports_switch_login_method(service._step_from_phase("totp")) is True
    assert service._supports_switch_login_method(service._step_from_phase("dp")) is True
    assert service._supports_switch_login_method(service._step_from_phase("pwd")) is True
    assert service._supports_switch_login_method(service._step_from_phase("bc")) is True
    assert service._supports_switch_login_method(service._step_from_phase("identifier")) is False
