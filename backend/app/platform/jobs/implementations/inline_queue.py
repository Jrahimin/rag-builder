"""Inline job queue for tests — runs document processing without Redis."""

from __future__ import annotations

import uuid

from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.names import DOCUMENT_PROCESS


class InlineJobQueue(JobQueue):
    """Execute supported jobs synchronously in-process (integration tests)."""

    async def enqueue(self, job: JobDefinition) -> str:
        job_id = job.idempotency_key or str(uuid.uuid4())
        if job.name != DOCUMENT_PROCESS:
            msg = f"Inline queue does not support job: {job.name!r}"
            raise JobEnqueueError(msg)

        from app.worker.handlers.document import run_document_process

        document_id = job.payload.get("document_id")
        if document_id is None:
            msg = "document.process payload requires document_id"
            raise JobEnqueueError(msg)

        await run_document_process(project_id=job.project_id, document_id=document_id)
        return job_id
