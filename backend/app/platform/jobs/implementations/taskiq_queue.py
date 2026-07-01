"""Taskiq-backed implementation of :class:`JobQueue`."""

from __future__ import annotations

import uuid

from taskiq.abc.broker import AsyncBroker

from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.names import DOCUMENT_PROCESS


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
        if job.name != DOCUMENT_PROCESS:
            msg = f"Unknown job name: {job.name!r}"
            raise JobEnqueueError(msg)

        from app.worker.handlers.document import document_process_task

        job_id = job.idempotency_key or str(uuid.uuid4())
        document_id = job.payload.get("document_id")
        if document_id is None:
            msg = "document.process payload requires document_id"
            raise JobEnqueueError(msg)

        try:
            await self._ensure_started()
            task = await (
                document_process_task.kicker()
                .with_task_id(job_id)
                .kiq(
                    project_id=str(job.project_id),
                    document_id=str(document_id),
                )
            )
        except Exception as exc:
            msg = f"Failed to enqueue job {job.name!r}"
            raise JobEnqueueError(msg, context={"job_name": job.name}) from exc

        return task.task_id

    async def close(self) -> None:
        if self._started:
            await self._broker.shutdown()
            self._started = False
