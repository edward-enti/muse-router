import asyncio

from ..core.pool import MuseAccount
from ..errors import AccountAuthExpiredError, UpstreamTransientError
from .base import DriverStreamChunk, MuseDriver

_LOGIN_MARKERS = ("/login", "/auth", "facebook.com", "meta.com/auth")


class BrowserCDPDriver(MuseDriver):
    """Playwright Chromium driver (SPEC §3.5 B.2).

    Playwright is imported lazily so that mock-mode tests never fail when
    Playwright is not installed. This driver is NOT verified live against
    muse.ai (no real account was provided); the streaming strategy follows
    the SPEC: network/DOM streamer first, DOM polling of the last assistant
    message as the safe fallback, then cookie refresh after completion.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._contexts: dict[str, object] = {}

    async def initialize(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise UpstreamTransientError("Playwright is not installed.") from exc
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)

    async def shutdown(self) -> None:
        self._contexts.clear()
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def _get_context(self, account: MuseAccount):
        if account.id in self._contexts:
            return self._contexts[account.id]
        if self._browser is None:
            await self.initialize()
        kwargs: dict = {"user_agent": account.user_agent}
        if account.proxy:
            kwargs["proxy"] = {"server": account.proxy}
        context = await self._browser.new_context(**kwargs)
        cookies = [
            {"name": k, "value": v, "domain": ".muse.ai", "path": "/"}
            for k, v in account.cookies.items()
        ]
        if cookies:
            await context.add_cookies(cookies)
        self._contexts[account.id] = context
        return context

    def _is_login_url(self, url: str) -> bool:
        return any(m in url for m in _LOGIN_MARKERS)

    async def generate_stream(self, account: MuseAccount, prompt: str, model: str, timeout: float = 120.0):
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:
            raise UpstreamTransientError("Playwright is not installed.") from exc
        context = await self._get_context(account)
        page = await context.new_page()
        try:
            await page.goto("https://muse.ai/", wait_until="domcontentloaded", timeout=30000)
            if self._is_login_url(page.url):
                raise AccountAuthExpiredError("Redirected to login page; cookie expired.")
            # Best-effort sign-in detection on the landing page.
            try:
                sign_in = await page.locator("text=Sign In").first.is_visible(timeout=2000)
            except Exception:
                sign_in = False
            if sign_in:
                raise AccountAuthExpiredError("Sign-in button present; cookie expired.")
            # Fill the composer and send.
            composer = None
            for selector in ("textarea", '[contenteditable="true"]', 'input[type="text"]'):
                try:
                    loc = page.locator(selector).first
                    if await loc.is_visible(timeout=2000):
                        composer = loc
                        break
                except Exception:
                    continue
            if composer is None:
                raise UpstreamTransientError("Could not find the muse.ai message composer.")
            await composer.fill(prompt)
            await page.keyboard.press("Enter")
            # DOM-polling fallback streamer: yield only newly appeared text.
            # The answer is considered complete once text has stopped growing
            # for 10 consecutive polls (~5s); this heuristic must be verified
            # live against muse.ai (see README disclaimer).
            old_text = ""
            stable_polls = 0
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                if asyncio.get_event_loop().time() > deadline:
                    raise UpstreamTransientError("Timed out waiting for muse.ai response.")
                try:
                    texts = await page.evaluate(
                        """() => Array.from(document.querySelectorAll('*'))
                            .filter(e => e.children.length === 0 && e.textContent && e.textContent.trim())
                            .map(e => e.textContent.trim())"""
                    )
                except Exception:
                    texts = []
                new_text = texts[-1] if texts else ""
                if new_text and len(new_text) > len(old_text):
                    yield DriverStreamChunk(text=new_text[len(old_text):])
                    old_text = new_text
                    stable_polls = 0
                elif old_text:
                    stable_polls += 1
                    if stable_polls >= 10:
                        break
                await asyncio.sleep(0.5)
        finally:
            try:
                cookies = await context.cookies()
                updated = {c["name"]: c["value"] for c in cookies if c["name"].startswith("hatch_")}
                if updated:
                    yield DriverStreamChunk(text="", updated_cookies=updated)
            except Exception:
                pass
            try:
                await page.close()
            except Exception:
                pass

    async def verify_account(self, account: MuseAccount) -> bool:
        try:
            context = await self._get_context(account)
            page = await context.new_page()
            try:
                await page.goto("https://muse.ai/", wait_until="domcontentloaded", timeout=30000)
                return not self._is_login_url(page.url)
            finally:
                await page.close()
        except AccountAuthExpiredError:
            return False
        except Exception:
            return False
