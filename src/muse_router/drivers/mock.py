import asyncio
from typing import AsyncIterator

from ..core.pool import MuseAccount
from ..errors import AccountAuthExpiredError, AccountQuotaExceededError
from .base import DriverStreamChunk, MuseDriver

SAMPLE_TEXT = "Hello! This is a sample response from Muse Spark."


class MockDriver(MuseDriver):
    """Mock driver for development and pytest (SPEC §3.5 B.1).

    Error simulation is driven by the account cookie value only (test tokens):
    - "expired_token" -> AccountAuthExpiredError
    - "ratelimit_token" -> AccountQuotaExceededError
    Otherwise a sample answer is streamed token-by-token (0.02s apart).
    As a deterministic tool-calling fixture, a prompt that explicitly names
    get_weather is answered with a <tool_call> block for that function.
    """

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def generate_stream(
        self, account: MuseAccount, prompt: str, model: str, timeout: float = 120.0
    ) -> AsyncIterator[DriverStreamChunk]:
        sess = account.cookies.get("hatch_sess", "")
        if sess == "expired_token":
            raise AccountAuthExpiredError("Mock account cookie has expired.")
        if sess == "ratelimit_token":
            raise AccountQuotaExceededError("Mock account is rate limited.")
        if "get_weather" in prompt:
            text = '<tool_call>{"name": "get_weather", "arguments": {"city": "Hanoi"}}</tool_call>'
            yield DriverStreamChunk(text=text)
            return
        words = SAMPLE_TEXT.split(" ")
        for i, word in enumerate(words):
            await asyncio.sleep(0.02)
            suffix = " " if i < len(words) - 1 else ""
            yield DriverStreamChunk(text=word + suffix)

    async def verify_account(self, account: MuseAccount) -> bool:
        return account.cookies.get("hatch_sess") not in ("expired_token", "")
