import asyncio
import logging
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, monitoring, servers
from app.core.config import Settings, get_settings
from app.core.database import create_database
from app.services.discovery import has_pending_events, publish_snapshot
from app.services.alert_history import collect_alert_history


def create_app(settings: Settings | None = None, *, database_url: str | None = None, discovery_dir: str | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    if database_url is not None:
        app_settings = app_settings.model_copy(update={"database_url": database_url})
    if discovery_dir is not None:
        app_settings = app_settings.model_copy(update={"prometheus_discovery_dir": discovery_dir})
    engine, factory = create_database(app_settings.database_url)
    logger = logging.getLogger("infrawatch")

    async def reconcile_discovery() -> None:
        try:
            await asyncio.to_thread(publish_snapshot, factory, app_settings.prometheus_discovery_dir)
        except Exception:
            logger.exception("Initial Prometheus discovery reconciliation failed; it will retry")
        while True:
            await asyncio.sleep(15)
            try:
                pending = await asyncio.to_thread(has_pending_events, factory)
                discovery_file = Path(app_settings.prometheus_discovery_dir) / "servers.json"
                if pending or not discovery_file.exists():
                    await asyncio.to_thread(publish_snapshot, factory, app_settings.prometheus_discovery_dir)
            except Exception:
                logger.exception("Prometheus discovery reconciliation failed; it will retry")
            try:
                await asyncio.to_thread(collect_alert_history, factory, app_settings.alertmanager_url)
            except Exception:
                logger.exception("Alert history collection failed; it will retry")

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        task = asyncio.create_task(reconcile_discovery(), name="prometheus-discovery-reconciler")
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        engine.dispose()

    application = FastAPI(
        title="InfraWatch API",
        version="1.0.0-rc.1",
        description="Authenticated infrastructure server registry and monitoring control plane.",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.session_factory = factory
    application.state.engine = engine
    application.state.logger = logger

    origins = app_settings.cors_origin_list
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            max_age=600,
        )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied) else str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        logger.exception("Unhandled API error request_id=%s", getattr(request.state, "request_id", "unknown"))
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @application.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @application.get("/health/ready", tags=["health"])
    def ready():
        with factory() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ready"}

    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(servers.router, prefix="/api/v1")
    application.include_router(monitoring.router, prefix="/api/v1")
    return application


app = create_app()
