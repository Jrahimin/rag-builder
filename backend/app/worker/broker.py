"""Taskiq broker — Redis list queue (compatible with Redis 3+ / Windows installs)."""

from __future__ import annotations

from functools import lru_cache

from taskiq_redis import ListQueueBroker

from app.core.config import get_settings


@lru_cache
def get_taskiq_broker() -> ListQueueBroker:
    """Return a process-scoped Taskiq broker configured from application settings."""
    settings = get_settings()
    # JobRun/outbox is the only retry authority; Taskiq is transport only.
    return ListQueueBroker(url=settings.redis.dsn)


# Module-level alias for ``taskiq worker app.worker.broker:broker``.
broker = get_taskiq_broker()
