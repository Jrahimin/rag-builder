"""Application factory and ASGI entrypoint.

``create_app`` builds and wires a fully configured FastAPI application:
configuration, structured logging, middleware, exception handlers, routers,
and lifespan-managed infrastructure (database, Redis, Qdrant).

The module-level ``app`` is the ASGI target used by uvicorn/gunicorn::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.v1.router import api_v1_router
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.db.qdrant import QdrantConnection
from app.db.redis import RedisClient
from app.db.session import Database

log = get_logger(__name__)

_DESCRIPTION = (
    "AI Platform Engine (APE) - deployable, provider-agnostic AI "
    "infrastructure exposed as Project-scoped REST APIs."
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create infrastructure clients on startup, dispose them on shutdown."""
    settings: Settings = app.state.settings
    log.info(
        "application_starting",
        environment=settings.app.env.value,
        version=settings.app.version,
    )

    # Instantiating clients does not open network connections yet.
    app.state.db = Database(settings)
    app.state.redis = RedisClient(settings)
    app.state.qdrant = QdrantConnection(settings)

    await _probe_dependencies(app)
    log.info("application_started")

    try:
        yield
    finally:
        log.info("application_stopping")
        await app.state.qdrant.dispose()
        await app.state.redis.dispose()
        await app.state.db.dispose()
        log.info("application_stopped")


async def _probe_dependencies(app: FastAPI) -> None:
    """Best-effort startup connectivity probe.

    Failures are logged but never abort startup: the app should boot and report
    degraded dependencies via ``/ready`` rather than crash-looping.
    """
    checks = {
        "postgresql": app.state.db,
        "redis": app.state.redis,
        "qdrant": app.state.qdrant,
    }
    for name, client in checks.items():
        try:
            await client.check()
            log.info("dependency_ready", dependency=name)
        except Exception as exc:
            log.warning(
                "dependency_unavailable_at_startup",
                dependency=name,
                error=str(exc),
            )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure a FastAPI application instance."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        description=_DESCRIPTION,
        docs_url=None if settings.app.is_production else "/docs",
        redoc_url=None if settings.app.is_production else "/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Middleware: the last added is outermost. RequestContext wraps everything
    # so every request (including CORS preflight) gets correlation IDs + logs.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allow_origins,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=settings.cors.allow_methods,
        allow_headers=settings.cors.allow_headers,
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_v1_router, prefix=settings.app.api_v1_prefix)

    return app


app = create_app()
