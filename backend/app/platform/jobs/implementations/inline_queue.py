"""Inline job queue for tests — runs supported jobs synchronously in-process."""

from __future__ import annotations

import uuid

from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.names import DOCUMENT_EMBED, DOCUMENT_INDEX, DOCUMENT_PROCESS, EVALUATION_RUN


class InlineJobQueue(JobQueue):
    """Execute supported jobs synchronously in-process (integration tests)."""

    async def enqueue(self, job: JobDefinition) -> str:
        job_id = job.idempotency_key or str(uuid.uuid4())
        durable_job_id = job.payload.get("job_id")
        if durable_job_id is None:
            msg = "Job payload requires job_id"
            raise JobEnqueueError(msg)

        if job.name == DOCUMENT_PROCESS:
            from app.worker.handlers.document import run_document_process

            await run_document_process(project_id=job.project_id, job_id=durable_job_id)
            return job_id

        if job.name == DOCUMENT_EMBED:
            from app.worker.handlers.embedding import run_document_embed

            await run_document_embed(project_id=job.project_id, job_id=durable_job_id)
            return job_id

        if job.name == DOCUMENT_INDEX:
            from app.worker.handlers.indexing import run_document_index

            await run_document_index(project_id=job.project_id, job_id=durable_job_id)
            return job_id

        if job.name == EVALUATION_RUN:
            from app.worker.handlers.evaluation import run_evaluation

            await run_evaluation(project_id=job.project_id, job_id=durable_job_id)
            return job_id

        msg = f"Inline queue does not support job: {job.name!r}"
        raise JobEnqueueError(msg)
