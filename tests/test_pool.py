import json

import pytest

from muse_router.core.pool import AccountPool, MuseAccount, parse_cookie_header
from muse_router.errors import NoAvailableAccountError
from tests.conftest import make_account


def test_pool_rotation_and_concurrency(tmp_path):
    accounts = [make_account(f"acc_{i}", f"token_{i}", max_concurrent=1) for i in (1, 2, 3)]
    pool = AccountPool(accounts=accounts, accounts_file=tmp_path / "accounts.json", strategy="round_robin")
    # Hold three leases simultaneously; rotation must be acc_1 -> acc_2 -> acc_3 -> acc_1.
    # (pytest-asyncio auto mode: this test itself is sync, so use asyncio.run via anyio? No:
    #  we run the async lease logic through a small async helper below.)
    import asyncio

    async def scenario():
        async with pool.acquire_lease() as a1:
            assert a1.id == "acc_1"
            async with pool.acquire_lease() as a2:
                assert a2.id == "acc_2"
                async with pool.acquire_lease() as a3:
                    assert a3.id == "acc_3"
                    with pytest.raises(NoAvailableAccountError):
                        async with pool.acquire_lease():
                            pass
                # a3 released; next lease goes to acc_3 again is also valid,
                # so instead verify rotation after all are released.
            assert a1.active_leases == 1
        assert all(a.active_leases == 0 for a in accounts)
        async with pool.acquire_lease() as again:
            assert again.id == "acc_1"

    asyncio.run(scenario())


def test_parse_raw_cookie_header():
    parsed = parse_cookie_header("hatch_sess=abc; hatch_gw=xyz; hatch_vml=123")
    assert parsed == {"hatch_sess": "abc", "hatch_gw": "xyz", "hatch_vml": "123"}
    # Also via account creation from a raw header.
    acc = MuseAccount(id="acc_x", label="x", cookies=parse_cookie_header("hatch_sess=abc; hatch_gw=xyz"))
    assert acc.cookies["hatch_sess"] == "abc"
    with pytest.raises(Exception):
        MuseAccount(id="bad", label="bad", cookies={"hatch_gw": "xyz"})


def test_atomic_persistence_resets_leases(tmp_path):
    import asyncio

    async def scenario():
        pool = AccountPool(accounts=[make_account("acc_1", "token_1")], accounts_file=tmp_path / "accounts.json")
        async with pool.acquire_lease():
            pass
        await pool.persist()
        raw = json.loads((tmp_path / "accounts.json").read_text())
        assert raw[0]["active_leases"] == 0
        pool2 = AccountPool(accounts_file=tmp_path / "accounts.json")
        pool2.load()
        assert pool2.list_accounts()[0].id == "acc_1"
        assert pool2.list_accounts()[0].active_leases == 0

    asyncio.run(scenario())


def test_cooldown_auto_recovery(tmp_path):
    import asyncio, time

    async def scenario():
        pool = AccountPool(accounts=[make_account("acc_1", "token_1")], accounts_file=tmp_path / "accounts.json")
        await pool.mark_cooldown("acc_1", -1, "test")  # already expired cooldown
        async with pool.acquire_lease() as acc:
            assert acc.status.value == "ready"

    asyncio.run(scenario())
