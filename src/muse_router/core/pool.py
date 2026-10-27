import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Optional

from pydantic import BaseModel, Field, field_validator

from ..errors import NoAvailableAccountError


class AccountStatus(str, Enum):
    READY = "ready"
    COOLING_DOWN = "cooling_down"
    EXPIRED = "expired"
    DISABLED = "disabled"


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    """Parse a raw `Cookie` header (as copied from DevTools) into a dict."""
    out: dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip().strip('"')
        if name:
            out[name] = value
    return out


class MuseAccount(BaseModel):
    id: str = Field(default_factory=lambda: f"acc_{uuid.uuid4().hex[:8]}")
    label: str = "account"
    cookies: dict[str, str]
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    proxy: Optional[str] = None
    status: AccountStatus = AccountStatus.READY
    max_concurrent: int = 2
    active_leases: int = 0
    cooldown_until: Optional[float] = None
    last_used_at: float = 0.0
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    last_error: Optional[str] = None

    @field_validator("cookies")
    @classmethod
    def validate_required_cookies(cls, v: dict[str, str]) -> dict[str, str]:
        if "hatch_sess" not in v or not str(v["hatch_sess"]).strip():
            raise ValueError("Missing required cookie: 'hatch_sess'")
        return v


class AccountPool:
    def __init__(
        self,
        accounts: Optional[list[MuseAccount]] = None,
        accounts_file: Path | str | None = None,
        strategy: str = "round_robin",
        default_max_concurrent: int = 2,
    ):
        self._accounts: list[MuseAccount] = list(accounts or [])
        self.accounts_file = Path(accounts_file) if accounts_file is not None else Path("data/accounts.json")
        self.strategy = strategy
        self.default_max_concurrent = default_max_concurrent
        self._lock = asyncio.Lock()

    # ---- persistence (atomic) ----
    def _dump(self) -> list[dict]:
        data = []
        for acc in self._accounts:
            d = acc.model_dump()
            d["active_leases"] = 0  # never persisted; always reset on startup
            data.append(d)
        return data

    def _persist_locked(self) -> None:
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.accounts_file.parent / (self.accounts_file.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._dump(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.accounts_file)

    async def persist(self) -> None:
        async with self._lock:
            self._persist_locked()

    def load(self) -> None:
        if not self.accounts_file.exists():
            return
        try:
            raw = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        except Exception:
            return
        accounts: list[MuseAccount] = []
        for item in raw:
            try:
                item = dict(item)
                item["active_leases"] = 0
                accounts.append(MuseAccount(**item))
            except Exception:
                continue
        self._accounts = accounts

    # ---- queries ----
    def list_accounts(self) -> list[MuseAccount]:
        return list(self._accounts)

    def get_account(self, account_id: str) -> Optional[MuseAccount]:
        for acc in self._accounts:
            if acc.id == account_id:
                return acc
        return None

    def _recover_locked(self) -> None:
        now = time.time()
        for acc in self._accounts:
            if acc.status == AccountStatus.COOLING_DOWN and acc.cooldown_until is not None and now >= acc.cooldown_until:
                acc.status = AccountStatus.READY
                acc.cooldown_until = None

    def is_available(self, account: MuseAccount, excluded_ids: set[str] | None = None) -> bool:
        if account.status != AccountStatus.READY:
            return False
        if account.active_leases >= account.max_concurrent:
            return False
        if excluded_ids and account.id in excluded_ids:
            return False
        return True

    # ---- selection ----
    def _select_locked(self, excluded_ids: set[str] | None, session_key: str | None) -> MuseAccount:
        self._recover_locked()
        excluded = excluded_ids or set()
        candidates = [a for a in self._accounts if self.is_available(a, excluded)]
        if not candidates:
            raise NoAvailableAccountError()
        if self.strategy == "affinity" and session_key:
            digest = hashlib.sha256(session_key.encode()).hexdigest()
            target = self._accounts[int(digest, 16) % len(self._accounts)] if self._accounts else None
            if target is not None and target in candidates:
                return target
            # fallback to least_loaded
            candidates.sort(key=lambda a: (a.active_leases / max(a.max_concurrent, 1), a.last_used_at))
            return candidates[0]
        if self.strategy == "least_loaded":
            candidates.sort(key=lambda a: (a.active_leases / max(a.max_concurrent, 1), a.last_used_at))
            return candidates[0]
        # round_robin (default): oldest last_used_at first, then least loaded
        candidates.sort(key=lambda a: (a.last_used_at, a.active_leases))
        return candidates[0]

    async def _select_and_increment(self, excluded_ids: set[str] | None, session_key: str | None) -> MuseAccount:
        async with self._lock:
            account = self._select_locked(excluded_ids, session_key)
            account.active_leases += 1
            account.last_used_at = time.time()
            return account

    async def _decrement_lease(self, account_id: str) -> None:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is not None and acc.active_leases > 0:
                acc.active_leases -= 1

    @asynccontextmanager
    async def acquire_lease(
        self, excluded_ids: set[str] | None = None, session_key: str | None = None
    ) -> AsyncIterator[MuseAccount]:
        account = await self._select_and_increment(excluded_ids, session_key)
        try:
            yield account
        finally:
            await self._decrement_lease(account.id)

    # ---- mutations (persisted) ----
    async def add_account(self, account: MuseAccount) -> MuseAccount:
        async with self._lock:
            if account.max_concurrent is None:
                account.max_concurrent = self.default_max_concurrent
            self._accounts.append(account)
            self._persist_locked()
        return account

    async def remove_account(self, account_id: str) -> bool:
        async with self._lock:
            before = len(self._accounts)
            self._accounts = [a for a in self._accounts if a.id != account_id]
            if len(self._accounts) != before:
                self._persist_locked()
                return True
            return False

    async def update_cookies(self, account_id: str, cookies: dict[str, str]) -> None:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is not None:
                acc.cookies.update(cookies)

    async def record_success(self, account_id: str) -> None:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is not None:
                acc.total_requests += 1
                acc.success_requests += 1

    async def record_failure(self, account_id: str, error: str) -> None:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is not None:
                acc.total_requests += 1
                acc.failed_requests += 1
                acc.last_error = error

    async def mark_expired(self, account_id: str, error: str = "") -> None:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is not None:
                acc.status = AccountStatus.EXPIRED
                acc.cooldown_until = None
                acc.failed_requests += 1
                acc.total_requests += 1
                if error:
                    acc.last_error = error
                self._persist_locked()

    async def mark_cooldown(self, account_id: str, seconds: float, error: str = "") -> None:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is not None:
                acc.status = AccountStatus.COOLING_DOWN
                acc.cooldown_until = time.time() + seconds
                acc.failed_requests += 1
                acc.total_requests += 1
                if error:
                    acc.last_error = error
                self._persist_locked()

    async def reset_account(self, account_id: str) -> Optional[MuseAccount]:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is not None:
                acc.status = AccountStatus.READY
                acc.cooldown_until = None
                acc.last_error = None
                self._persist_locked()
            return acc

    async def update_account(self, account_id: str, **fields) -> Optional[MuseAccount]:
        async with self._lock:
            acc = self.get_account(account_id)
            if acc is None:
                return None
            for key, value in fields.items():
                if value is None:
                    continue
                if key == "cookies":
                    acc.cookies = dict(value)
                elif key == "status" and isinstance(value, str):
                    acc.status = AccountStatus(value.lower())
                elif hasattr(acc, key):
                    setattr(acc, key, value)
            self._persist_locked()
            return acc
