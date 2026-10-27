from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...core.pool import AccountStatus, MuseAccount, parse_cookie_header
from ...errors import MuseRouterError
from ..deps import get_pool, require_admin_key
from ..schemas import AccountCreateRequest, AccountUpdateRequest

router = APIRouter()


def mask_cookie(value: str) -> str:
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def public_account(acc: MuseAccount) -> dict:
    cookies = {}
    for k, v in acc.cookies.items():
        cookies[k] = mask_cookie(v) if k == "hatch_sess" else v
    return {
        "id": acc.id,
        "label": acc.label,
        "status": acc.status.value,
        "cookies": cookies,
        "hatch_sess_masked": mask_cookie(acc.cookies.get("hatch_sess", "")),
        "proxy": acc.proxy,
        "max_concurrent": acc.max_concurrent,
        "active_leases": acc.active_leases,
        "cooldown_until": acc.cooldown_until,
        "last_used_at": acc.last_used_at,
        "total_requests": acc.total_requests,
        "success_requests": acc.success_requests,
        "failed_requests": acc.failed_requests,
        "last_error": acc.last_error,
    }


def stats(pool) -> dict:
    accounts = pool.list_accounts()
    return {
        "total": len(accounts),
        "ready": sum(1 for a in accounts if a.status == AccountStatus.READY),
        "cooling_down": sum(1 for a in accounts if a.status == AccountStatus.COOLING_DOWN),
        "expired": sum(1 for a in accounts if a.status == AccountStatus.EXPIRED),
        "disabled": sum(1 for a in accounts if a.status == AccountStatus.DISABLED),
        "active_leases_total": sum(a.active_leases for a in accounts),
    }


@router.get("/admin/accounts", dependencies=[Depends(require_admin_key)])
async def list_accounts(pool=Depends(get_pool)):
    return {"accounts": [public_account(a) for a in pool.list_accounts()], **stats(pool)}


@router.get("/admin/stats", dependencies=[Depends(require_admin_key)])
async def get_stats(pool=Depends(get_pool)):
    return stats(pool)


@router.post("/admin/accounts", dependencies=[Depends(require_admin_key)])
async def create_accounts(request: Request, pool=Depends(get_pool)):
    body = await request.json()
    items = body if isinstance(body, list) else [body]
    created = []
    for item in items:
        req = AccountCreateRequest(**item)
        kwargs: dict = {
            "label": req.label,
            "cookies": req.cookies or {},
            "proxy": req.proxy,
            "max_concurrent": req.max_concurrent or pool.default_max_concurrent,
        }
        if req.user_agent:
            kwargs["user_agent"] = req.user_agent
        if req.id:
            kwargs["id"] = req.id
        acc = MuseAccount(**kwargs)
        await pool.add_account(acc)
        created.append(public_account(acc))
    if isinstance(body, list):
        return {"accounts": created}
    return created[0]


@router.patch("/admin/accounts/{account_id}", dependencies=[Depends(require_admin_key)])
async def update_account(account_id: str, payload: AccountUpdateRequest, pool=Depends(get_pool)):
    acc = pool.get_account(account_id)
    if acc is None:
        raise MuseRouterError("Account not found.", status_code=404, error_type="invalid_request_error", code="account_not_found")
    fields: dict = {}
    if payload.label is not None:
        fields["label"] = payload.label
    if payload.cookies is not None:
        fields["cookies"] = payload.cookies
    if payload.proxy is not None:
        fields["proxy"] = payload.proxy
    if payload.max_concurrent is not None:
        fields["max_concurrent"] = payload.max_concurrent
    if payload.status is not None:
        fields["status"] = payload.status
    updated = await pool.update_account(account_id, **fields)
    return public_account(updated) if updated else {}


@router.post("/admin/accounts/{account_id}/reset", dependencies=[Depends(require_admin_key)])
async def reset_account(account_id: str, pool=Depends(get_pool)):
    acc = await pool.reset_account(account_id)
    if acc is None:
        raise MuseRouterError("Account not found.", status_code=404, error_type="invalid_request_error", code="account_not_found")
    return public_account(acc)


@router.delete("/admin/accounts/{account_id}", dependencies=[Depends(require_admin_key)])
async def delete_account(account_id: str, pool=Depends(get_pool)):
    ok = await pool.remove_account(account_id)
    if not ok:
        raise MuseRouterError("Account not found.", status_code=404, error_type="invalid_request_error", code="account_not_found")
    return {"deleted": account_id}
