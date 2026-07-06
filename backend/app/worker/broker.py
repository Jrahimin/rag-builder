"""Taskiq broker — Redis list queue (compatible with Redis 3+ / Windows installs)."""

from __future__ import annotations

from functools import lru_cache

from taskiq.middlewares import SmartRetryMiddleware
from taskiq_redis import ListQueueBroker

from app.core.config import get_settings


@lru_cache
def get_taskiq_broker() -> ListQueueBroker:
    """Return a process-scoped Taskiq broker configured from application settings."""
    settings = get_settings()
    broker = ListQueueBroker(url=settings.redis.dsn)
    # Retries are opt-in per task via labels set from JobDefinition.retry
    # (see platform/jobs/registry.py); delay/backoff come from those labels.
    broker.add_middlewares(
        SmartRetryMiddleware(
            default_retry_label=False,
            use_jitter=True,
            use_delay_exponent=True,
            max_delay_exponent=300,
        )
    )
    return broker


# Module-level alias for ``taskiq worker app.worker.broker:broker``.
broker = get_taskiq_broker()
