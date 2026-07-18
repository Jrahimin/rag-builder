"""Composition helpers for retrieval services used by API, workers, and CLIs."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.jobs import build_job_service
from app.core.config import Settings
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter, JobQueue
from app.platform.jobs.implementations.job_queue_factory import create_job_queue


def build_indexing_service(
    *,
    session: AsyncSession,
    project_id: uuid.UUID,
    settings: Settings,
    job_submitter: DurableJobSubmitter | None = None,
    job_queue: JobQueue | None = None,
) -> IndexingService:
    """Wire retrieval indexing from one explicit settings snapshot."""
    queue = job_queue if job_queue is not None else create_job_queue(settings)
    submitter = job_submitter or build_job_service(
        session=session,
        project_id=project_id,
        settings=settings,
        queue=queue,
    )
    return IndexingService(
        session=session,
        project_id=project_id,
        job_submitter=submitter,
        job_configuration=build_job_configuration(settings),
        retrieval_config=settings.retrieval,
        job_max_attempts=settings.jobs.max_attempts,
    )
