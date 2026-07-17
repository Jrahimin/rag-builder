"""Taskiq broker — Redis list queue (compatible with Redis 3+ / Windows installs)."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from functools import lru_cache

from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import ListQueueBroker

from app.core.auth_config_validation import validate_auth_config
from app.core.config import get_settings
from app.core.runtime_validation import validate_runtime_config
from app.platform.db.session import Database
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.jobs.worker_registry import (
    WorkerRegistry,
    create_worker_id,
    run_worker_heartbeat_loop,
)
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.platform.system.preflight_service import StartupPreflightService


@lru_cache
def get_taskiq_broker() -> ListQueueBroker:
    """Return a process-scoped Taskiq broker configured from application settings."""
    settings = get_settings()
    validate_runtime_config(settings)
    validate_auth_config(settings)
    # JobRun/outbox is the only retry authority; Taskiq is transport only.
    return ListQueueBroker(url=settings.redis.dsn)


# Module-level alias for ``taskiq worker app.worker.broker:broker``.
broker = get_taskiq_broker()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def startup_worker_heartbeat(state: TaskiqState) -> None:
    """Register the worker before it begins consuming tasks."""
    settings = get_settings()
    connectivity = RedisConnectivity(settings)
    database = Database(settings)
    storage = create_storage_provider(settings)
    try:
        preflight = await StartupPreflightService(
            settings=settings,
            database=database,
            redis=connectivity,
            storage=storage,
        ).run()
    except Exception:
        await connectivity.dispose()
        await database.dispose()
        raise
    registry = WorkerRegistry(connectivity.client, settings)
    worker_id = create_worker_id()
    started_at = datetime.now(UTC)
    task = asyncio.create_task(
        run_worker_heartbeat_loop(
            registry,
            settings,
            worker_id=worker_id,
            started_at=started_at,
        ),
        name="worker-availability-heartbeat",
    )
    state.ape_worker_connectivity = connectivity
    state.ape_worker_database = database
    state.ape_worker_preflight = preflight
    state.ape_worker_registry = registry
    state.ape_worker_id = worker_id
    state.ape_worker_heartbeat_task = task


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def shutdown_worker_heartbeat(state: TaskiqState) -> None:
    """Remove the worker identity on graceful shutdown; TTL covers crashes."""
    task: asyncio.Task[None] = state.ape_worker_heartbeat_task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    registry: WorkerRegistry = state.ape_worker_registry
    await registry.remove(state.ape_worker_id)
    connectivity: RedisConnectivity = state.ape_worker_connectivity
    await connectivity.dispose()
    database: Database = state.ape_worker_database
    await database.dispose()
