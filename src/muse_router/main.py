import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .api.routes import admin, chat, dashboard, health, models
from .config import Settings
from .core.activity import ActivityLog
from .core.gateway import Gateway
from .core.keepalive import keepalive_worker
from .core.pool import AccountPool
from .drivers.browser_cdp import BrowserCDPDriver
from .drivers.http_direct import DirectHTTPDriver
from .drivers.mock import MockDriver
from .errors import MuseRouterError

logger = logging.getLogger(__name__)


def build_driver(settings: Settings):
    if settings.driver == "mock":
        return MockDriver()
    if settings.driver == "http":
        return DirectHTTPDriver()
    return BrowserCDPDriver(
        headless=settings.headless,
        debug_dir=settings.browser_debug_dir,
        debug_keep_page=settings.browser_debug_keep_page,
    )


def create_app(settings: Settings | None = None, pool: AccountPool | None = None, driver=None, gateway: Gateway | None = None) -> FastAPI:
    settings = settings or Settings()
    driver = driver or build_driver(settings)
    if pool is None:
        pool = AccountPool(
            accounts_file=settings.accounts_file,
            strategy=settings.pool_strategy,
            default_max_concurrent=settings.default_max_concurrent,
        )
        pool.load()
    gateway = gateway or Gateway(pool, driver, settings)
    activity = ActivityLog()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.pool = pool
        app.state.driver = driver
        app.state.gateway = gateway
        app.state.activity = activity
        keepalive_task = None
        try:
            await driver.initialize()
        except Exception as exc:
            logger.warning("Driver initialisation failed (continuing): %s", exc)
        if settings.keepalive_interval_sec > 0:
            keepalive_task = asyncio.create_task(keepalive_worker(pool, driver, settings.keepalive_interval_sec))
        try:
            yield
        finally:
            if keepalive_task is not None:
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass
            try:
                await driver.shutdown()
            except Exception as exc:
                logger.warning("Driver shutdown failed: %s", exc)

    app = FastAPI(title="muse-router", version="0.1.0", lifespan=lifespan)
    # app.state is also set here so TestClient usage without lifespan still works.
    app.state.settings = settings
    app.state.pool = pool
    app.state.driver = driver
    app.state.gateway = gateway
    app.state.activity = activity

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(MuseRouterError)
    async def muse_router_error_handler(request: Request, exc: MuseRouterError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_openai_error())

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            content = detail
        else:
            content = {"error": {"message": str(detail), "type": "invalid_request_error", "param": None, "code": None}}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"message": str(exc), "type": "invalid_request_error", "param": None, "code": "validation_error"}},
        )

    app.include_router(models.router)
    app.include_router(chat.router)
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(dashboard.router)
    return app


settings = Settings()
app = create_app(settings=settings)


def run() -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
