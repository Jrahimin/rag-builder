"""Job queue factory — Taskiq in production, optional inline runner for tests."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import JobQueueBackend, Settings, get_settings
from app.platform.jobs.contracts import JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.implementations.inline_queue import InlineJobQueue
from app.platform.jobs.implementations.taskiq_queue import TaskiqJobQueue
from app.worker.broker import get_taskiq_broker


@lru_cache
def get_job_queue() -> JobQueue:
    """Return a process-scoped job queue from application settings."""
    return create_job_queue(get_settings())


def create_job_queue(settings: Settings) -> JobQueue:
    backend = settings.jobs.backend
    if backend == JobQueueBackend.TASKIQ:
        return TaskiqJobQueue(get_taskiq_broker())
    if backend == JobQueueBackend.INLINE:
        return InlineJobQueue()
    msg = f"Unsupported job queue backend: {backend!r}"
    raise JobEnqueueError(msg)
