from fastapi import APIRouter, Request

from ...core.pool import AccountStatus

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    pool = getattr(request.app.state, "pool", None)
    accounts = pool.list_accounts() if pool is not None else []
    ready = sum(1 for a in accounts if a.status == AccountStatus.READY)
    return {"status": "ready", "accounts_total": len(accounts), "accounts_ready": ready}
