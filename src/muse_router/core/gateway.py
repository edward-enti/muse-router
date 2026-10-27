import asyncio
from typing import AsyncIterator

from ..config import Settings
from ..drivers.base import MuseDriver
from ..errors import (
    AccountAuthExpiredError,
    AccountQuotaExceededError,
    MuseRouterError,
    UpstreamTransientError,
)
from .pool import AccountPool


class Gateway:
    def __init__(self, pool: AccountPool, driver: MuseDriver, settings: Settings):
        self.pool = pool
        self.driver = driver
        self.settings = settings

    async def _handle_account_exception(self, account_id: str, err: Exception) -> None:
        if isinstance(err, AccountAuthExpiredError):
            await self.pool.mark_expired(account_id, str(err))
        elif isinstance(err, AccountQuotaExceededError):
            await self.pool.mark_cooldown(account_id, self.settings.quota_cooldown_sec, str(err))
        elif isinstance(err, (UpstreamTransientError, MuseRouterError)):
            await self.pool.mark_cooldown(account_id, self.settings.transient_cooldown_sec, str(err))
        else:
            await self.pool.mark_cooldown(account_id, self.settings.transient_cooldown_sec, str(err))

    async def execute_with_failover(
        self, prompt: str, model: str, session_key: str | None = None
    ) -> tuple[object, AsyncIterator[str]]:
        excluded_ids: set[str] = set()
        last_error: Exception | None = None

        for _attempt in range(self.settings.max_retries):
            # Acquire outside the stream try-block, as in the SPEC pseudocode:
            # a lease failure (no account available) propagates immediately.
            cm = self.pool.acquire_lease(excluded_ids=excluded_ids, session_key=session_key)
            account = await cm.__aenter__()
            excluded_ids.add(account.id)

            try:
                raw_stream = self.driver.generate_stream(
                    account=account,
                    prompt=prompt,
                    model=model,
                    timeout=self.settings.request_timeout_sec,
                )
                try:
                    first_chunk = await asyncio.wait_for(
                        anext(raw_stream), timeout=self.settings.first_chunk_timeout_sec
                    )
                except asyncio.TimeoutError as exc:
                    # Close the upstream stream, release the lease, cooldown.
                    try:
                        await raw_stream.aclose()
                    except Exception:
                        pass
                    await cm.__aexit__(None, None, None)
                    last_error = UpstreamTransientError("Timed out waiting for the first chunk.")
                    await self.pool.mark_cooldown(account.id, self.settings.transient_cooldown_sec, str(last_error))
                    continue

                async def _wrapped_stream() -> AsyncIterator[str]:
                    try:
                        if first_chunk.updated_cookies:
                            await self.pool.update_cookies(account.id, first_chunk.updated_cookies)
                        yield first_chunk.text
                        async for chunk in raw_stream:
                            if chunk.updated_cookies:
                                await self.pool.update_cookies(account.id, chunk.updated_cookies)
                            if chunk.text:
                                yield chunk.text
                        await self.pool.record_success(account.id)
                    except Exception as mid_stream_err:
                        await self._handle_account_exception(account.id, mid_stream_err)
                        raise
                    finally:
                        await cm.__aexit__(None, None, None)

                return account, _wrapped_stream()

            except StopAsyncIteration:
                await cm.__aexit__(None, None, None)
                last_error = UpstreamTransientError("Upstream returned an empty response stream.")
                await self.pool.mark_cooldown(account.id, self.settings.transient_cooldown_sec, str(last_error))

            except Exception as err:
                await cm.__aexit__(type(err), err, err.__traceback__)
                last_error = err
                await self._handle_account_exception(account.id, err)

        if isinstance(last_error, MuseRouterError):
            raise last_error
        raise UpstreamTransientError(f"All retry attempts failed. Last error: {last_error}")
