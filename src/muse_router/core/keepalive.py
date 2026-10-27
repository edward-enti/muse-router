import asyncio
import logging

from ..drivers.base import MuseDriver
from .pool import AccountPool, AccountStatus

logger = logging.getLogger(__name__)


async def keepalive_worker(pool: AccountPool, driver: MuseDriver, interval_sec: int) -> None:
    """Background worker: periodically verify READY accounts. 0 = disabled."""
    if interval_sec <= 0:
        return
    while True:
        await asyncio.sleep(interval_sec)
        for account in pool.list_accounts():
            if account.status != AccountStatus.READY:
                continue
            try:
                ok = await driver.verify_account(account)
                if not ok:
                    await pool.mark_expired(account.id, "Keepalive verification failed.")
            except Exception as exc:  # never crash the app on a keepalive failure
                logger.warning("Keepalive failed for %s: %s", account.id, exc)
