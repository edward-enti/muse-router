"""Dashboard routes for muse-router.

GET / and GET /dashboard serve a single self-contained HTML page
(no CDN, no external assets, no template engine). All data endpoints
require the admin key, except the static page itself, which contains
no secrets at all. /admin/config deliberately exposes only
non-sensitive settings: API/admin keys are never returned, under
any field name.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ...config import Settings
from ...core.activity import activity_from_app
from ..deps import get_pool, get_settings, require_admin_key
from .admin import stats as pool_stats

router = APIRouter()

# routes/dashboard.py -> routes -> api -> muse_router -> web/index.html
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
INDEX_HTML = WEB_DIR / "index.html"

# Non-sensitive settings only. Never add api_key / admin_key here.
SAFE_CONFIG_FIELDS = (
    "host",
    "port",
    "driver",
    "pool_strategy",
    "max_retries",
    "default_max_concurrent",
    "quota_cooldown_sec",
    "transient_cooldown_sec",
    "request_timeout_sec",
    "first_chunk_timeout_sec",
    "headless",
    "keepalive_interval_sec",
    "accounts_file",
)


def _dashboard_html() -> str:
    # Read on every request so edits to the page show up without a restart.
    return INDEX_HTML.read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse)
async def dashboard_root():
    return HTMLResponse(content=_dashboard_html())


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return HTMLResponse(content=_dashboard_html())


@router.get("/admin/activity", dependencies=[Depends(require_admin_key)])
async def list_activity(request: Request, limit: int = 100):
    activity = activity_from_app(request.app)
    return {"events": activity.recent(limit=limit)}


@router.post("/admin/activity/clear", dependencies=[Depends(require_admin_key)])
async def clear_activity(request: Request):
    activity_from_app(request.app).clear()
    return {"cleared": True}


@router.get("/admin/usage", dependencies=[Depends(require_admin_key)])
async def usage(request: Request, pool=Depends(get_pool)):
    activity = activity_from_app(request.app)
    summary = activity.summary()
    return {**summary, "pool": pool_stats(pool)}


@router.get("/admin/config", dependencies=[Depends(require_admin_key)])
async def safe_config(settings: Settings = Depends(get_settings)):
    out = {}
    for field in SAFE_CONFIG_FIELDS:
        value = getattr(settings, field)
        out[field] = str(value) if field == "accounts_file" else value
    return out
