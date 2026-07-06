"""Inline job queue for tests — runs supported jobs synchronously in-process."""

from __future__ import annotations

import uuid

from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.names import DOCUMENT_EMBED, DOCUMENT_INDEX, DOCUMENT_PROCESS


class InlineJobQueue(JobQueue):
    """Execute supported jobs synchronously in-process (integration tests)."""

    async def enqueue(self, job: JobDefinition) -> str:
        job_id = job.idempotency_key or str(uuid.uuid4())
        document_id = job.payload.get("document_id")
        if document_id is None:
            msg = "Job payload requires document_id"
            raise JobEnqueueError(msg)

        if job.name == DOCUMENT_PROCESS:
            from app.worker.handlers.document import run_document_process

            await run_document_process(project_id=job.project_id, document_id=document_id)
            return job_id

        if job.name == DOCUMENT_EMBED:
            from app.worker.handlers.embedding import run_document_embed

            await run_document_embed(project_id=job.project_id, document_id=document_id)
            return job_id

        if job.name == DOCUMENT_INDEX:
            from app.worker.handlers.indexing import run_document_index

            await run_document_index(project_id=job.project_id, document_id=document_id)
            return job_id

        msg = f"Inline queue does not support job: {job.name!r}"
        raise JobEnqueueError(msg)
