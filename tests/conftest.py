import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from muse_router.config import Settings  # noqa: E402
from muse_router.core.gateway import Gateway  # noqa: E402
from muse_router.core.pool import AccountPool, MuseAccount  # noqa: E402
from muse_router.drivers.mock import MockDriver  # noqa: E402
from muse_router.main import create_app  # noqa: E402


def make_settings(tmp_path, **overrides):
    kwargs = dict(
        api_key="sk-test-key",
        admin_key="sk-test-admin-key",
        driver="mock",
        accounts_file=tmp_path / "accounts.json",
        keepalive_interval_sec=0,
        first_chunk_timeout_sec=10.0,
        request_timeout_sec=30.0,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_account(acc_id: str, sess: str, max_concurrent: int = 2) -> MuseAccount:
    return MuseAccount(id=acc_id, label=acc_id, cookies={"hatch_sess": sess}, max_concurrent=max_concurrent)


def make_pool(tmp_path, accounts, **overrides):
    settings = make_settings(tmp_path, **overrides)
    pool = AccountPool(accounts=list(accounts), accounts_file=settings.accounts_file,
                       strategy=settings.pool_strategy, default_max_concurrent=settings.default_max_concurrent)
    return pool, settings


def make_client(tmp_path, accounts, **overrides):
    pool, settings = make_pool(tmp_path, accounts, **overrides)
    gateway = Gateway(pool, MockDriver(), settings)
    app = create_app(settings=settings, pool=pool, driver=MockDriver(), gateway=gateway)
    client = TestClient(app, raise_server_exceptions=False)
    return client, pool, settings


@pytest.fixture()
def api_key_headers():
    return {"Authorization": "Bearer sk-test-key"}


@pytest.fixture()
def admin_headers():
    return {"Authorization": "Bearer sk-test-admin-key"}
