"""Taskiq-backed implementation of :class:`JobQueue`."""

from __future__ import annotations

import uuid

from taskiq.abc.broker import AsyncBroker

from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.registry import get_job_registry


class TaskiqJobQueue(JobQueue):
    """Enqueue jobs on Redis for Taskiq workers."""

    def __init__(self, broker: AsyncBroker) -> None:
        self._broker = broker
        self._started = False

    async def _ensure_started(self) -> None:
        if not self._started:
            await self._broker.startup()
            self._started = True

    async def enqueue(self, job: JobDefinition) -> str:
        registry = get_job_registry()
        spec = registry.get(job.name)
        if spec is None:
            msg = f"Unknown job name: {job.name!r}"
            raise JobEnqueueError(msg)

        job_id = job.idempotency_key or str(uuid.uuid4())
        document_id, _ = spec.validate_payload(job.payload)

        try:
            await self._ensure_started()
            task = await spec.enqueue(
                job_id=job_id,
                project_id=job.project_id,
                document_id=document_id,
                retry=job.retry,
            )
        except JobEnqueueError:
            raise
        except Exception as exc:
            msg = f"Failed to enqueue job {job.name!r}"
            raise JobEnqueueError(msg, context={"job_name": job.name}) from exc

        return task.task_id

    async def close(self) -> None:
        if self._started:
            await self._broker.shutdown()
            self._started = False
