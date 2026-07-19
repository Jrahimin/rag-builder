"""Application factory and ASGI entrypoint.

``create_app`` builds and wires a fully configured FastAPI application:
configuration, structured logging, middleware, exception handlers, routers,
and lifespan-managed infrastructure (PostgreSQL and Redis).

The module-level ``app`` is the ASGI target used by uvicorn/gunicorn. From
``backend/``, either::

    python -m app
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8088

``python -m app`` reads host, port, and reload from ``backend/.env``
(``APE_SERVER__*``). The uvicorn command sets those values explicitly on the
CLI (useful when you want to override without editing ``.env``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.v1.router import api_v1_router
from app.composition.jobs import DurableJobDispatcher, stop_dispatcher_task
from app.composition.webhooks import WebhookDispatcher, stop_webhook_dispatcher
from app.core.auth_config_validation import validate_auth_config
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.runtime_validation import validate_runtime_config
from app.platform.db.session import Database
from app.platform.http.openapi_security import configure_openapi_security
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.jobs.implementations.job_queue_factory import get_job_queue
from app.platform.jobs.implementations.taskiq_queue import TaskiqJobQueue
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.platform.system.preflight_service import StartupPreflightService

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
    app.state.redis = RedisConnectivity(settings)
    app.state.storage = create_storage_provider(settings)
    dispatcher: DurableJobDispatcher | None = None
    dispatcher_task: asyncio.Task[None] | None = None
    webhook_dispatcher: WebhookDispatcher | None = None
    webhook_dispatcher_task: asyncio.Task[None] | None = None
    queue = get_job_queue()
    try:
        app.state.preflight = await StartupPreflightService(
            settings=settings,
            database=app.state.db,
            redis=app.state.redis,
            storage=app.state.storage,
        ).run()
        if settings.jobs.dispatcher_enabled:
            dispatcher = DurableJobDispatcher(
                session_factory=app.state.db.session_factory,
                settings=settings,
                queue=queue,
            )
            dispatcher_task = asyncio.create_task(
                dispatcher.run_forever(),
                name="durable-job-dispatcher",
            )
        if settings.webhooks.enabled and settings.webhooks.dispatcher_enabled:
            webhook_dispatcher = WebhookDispatcher(
                session_factory=app.state.db.session_factory,
                settings=settings,
            )
            webhook_dispatcher_task = asyncio.create_task(
                webhook_dispatcher.run_forever(),
                name="webhook-dispatcher",
            )
        log.info(
            "application_started",
            runtime_profile=settings.runtime.profile.value,
            preflight_status=app.state.preflight.status,
        )
        yield
    finally:
        log.info("application_stopping")
        await stop_dispatcher_task(dispatcher, dispatcher_task)
        await stop_webhook_dispatcher(webhook_dispatcher, webhook_dispatcher_task)
        if isinstance(queue, TaskiqJobQueue):
            await queue.close()
        await app.state.redis.dispose()
        await app.state.db.dispose()
        log.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure a FastAPI application instance."""
    settings = settings or get_settings()
    validate_runtime_config(settings)
    validate_auth_config(settings)
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
    configure_openapi_security(app)

    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(api_v1_router, prefix=settings.app.api_v1_prefix)

    return app


app = create_app()
