from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from pydantic import BaseModel

from ..core.pool import MuseAccount


class DriverStreamChunk(BaseModel):
    text: str
    updated_cookies: Optional[dict[str, str]] = None


class MuseDriver(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        """Initialise resources (e.g. start a Playwright browser instance)."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release resources on server shutdown."""

    @abstractmethod
    async def generate_stream(
        self, account: MuseAccount, prompt: str, model: str, timeout: float = 120.0
    ) -> AsyncIterator[DriverStreamChunk]:
        """Send a prompt to muse.ai and yield delta text chunks."""

    @abstractmethod
    async def verify_account(self, account: MuseAccount) -> bool:
        """Quickly check whether an account is still valid."""
