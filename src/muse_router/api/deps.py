from fastapi import Request

from ..config import Settings
from ..core.gateway import Gateway
from ..core.pool import AccountPool
from ..errors import MuseRouterError


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return None


async def require_api_key(request: Request) -> None:
    settings: Settings = request.app.state.settings
    token = _bearer(request)
    if not token or token not in settings.api_keys:
        raise MuseRouterError(
            "Incorrect API key provided.", status_code=401,
            error_type="invalid_request_error", code="invalid_api_key",
        )


async def require_admin_key(request: Request) -> None:
    settings: Settings = request.app.state.settings
    token = _bearer(request)
    if not token or token not in settings.admin_keys:
        raise MuseRouterError(
            "Incorrect admin key provided.", status_code=401,
            error_type="invalid_request_error", code="invalid_admin_key",
        )


def get_pool(request: Request) -> AccountPool:
    return request.app.state.pool


def get_gateway(request: Request) -> Gateway:
    return request.app.state.gateway


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
