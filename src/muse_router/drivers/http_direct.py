import httpx

from ..core.pool import MuseAccount
from ..errors import AccountAuthExpiredError, AccountQuotaExceededError, UpstreamTransientError
from .base import DriverStreamChunk, MuseDriver

# NOTE: the SPEC does not name the real muse.ai conversation endpoint, so this
# URL is a best-effort placeholder. This driver is NOT verified live (see README).
_UPSTREAM_URL = "https://muse.ai/api/chat"


class DirectHTTPDriver(MuseDriver):
    """Direct HTTP driver (SPEC §3.5 B.3) with SPEC status-code mapping."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _cookie_header(self, account: MuseAccount) -> str:
        return "; ".join(f"{k}={v}" for k, v in account.cookies.items())

    async def generate_stream(self, account: MuseAccount, prompt: str, model: str, timeout: float = 120.0):
        if self._client is None:
            await self.initialize()
        assert self._client is not None
        try:
            resp = await self._client.post(
                _UPSTREAM_URL,
                json={"prompt": prompt, "model": model},
                headers={"Cookie": self._cookie_header(account), "User-Agent": account.user_agent},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise UpstreamTransientError(f"Upstream timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamTransientError(f"Upstream network error: {exc}") from exc
        if resp.status_code in (401, 403):
            raise AccountAuthExpiredError(f"Upstream returned {resp.status_code}.")
        if resp.status_code == 429:
            raise AccountQuotaExceededError(f"Upstream returned {resp.status_code}.")
        if resp.status_code >= 500:
            raise UpstreamTransientError(f"Upstream returned {resp.status_code}.")
        try:
            data = resp.json()
        except Exception:
            data = {}
        text = ""
        if isinstance(data, dict):
            text = str(data.get("response") or data.get("text") or data.get("content") or data.get("message") or "")
        if not text:
            text = resp.text
        yield DriverStreamChunk(text=text)

    async def verify_account(self, account: MuseAccount) -> bool:
        try:
            if self._client is None:
                await self.initialize()
            assert self._client is not None
            resp = await self._client.get(
                "https://muse.ai/",
                headers={"Cookie": self._cookie_header(account), "User-Agent": account.user_agent},
                timeout=30.0,
                follow_redirects=False,
            )
            return resp.status_code not in (401, 403)
        except Exception:
            return False
