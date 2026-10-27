from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MUSE_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 18610
    api_key: str = "sk-muse-default-key"
    admin_key: str = "sk-muse-admin-key"
    driver: Literal["mock", "browser", "http"] = "browser"
    accounts_file: Path = Path("data/accounts.json")
    pool_strategy: Literal["round_robin", "least_loaded", "affinity"] = "round_robin"
    max_retries: int = 3
    default_max_concurrent: int = 2
    quota_cooldown_sec: int = 1800
    transient_cooldown_sec: int = 45
    request_timeout_sec: float = 120.0
    first_chunk_timeout_sec: float = 25.0
    headless: bool = True
    browser_debug_dir: Path = Path("debug/browser")
    browser_debug_keep_page: bool = False
    keepalive_interval_sec: int = 900

    @property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.api_key.split(",") if k.strip()]

    @property
    def admin_keys(self) -> list[str]:
        return [k.strip() for k in self.admin_key.split(",") if k.strip()]
