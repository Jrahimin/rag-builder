"""Composition helpers for retrieval services used by API, workers, and CLIs."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.jobs import build_job_service
from app.core.config import Settings
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.config.project_ai import (
    EffectiveConfigResolution,
    apply_effective_ai_config,
    resolve_project_ai_config,
)
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
    resolution: EffectiveConfigResolution | None = None,
    active_index_build_id: str | None = None,
    source_metadata_generation: int = 0,
) -> IndexingService:
    """Wire retrieval indexing from one explicit settings snapshot."""
    effective_resolution = resolution or resolve_project_ai_config(settings, None)
    effective_settings = apply_effective_ai_config(settings, effective_resolution)
    queue = job_queue if job_queue is not None else create_job_queue(effective_settings)
    submitter = job_submitter or build_job_service(
        session=session,
        project_id=project_id,
        settings=effective_settings,
        queue=queue,
    )
    return IndexingService(
        session=session,
        project_id=project_id,
        job_submitter=submitter,
        job_configuration=build_job_configuration(
            settings,
            resolution=effective_resolution,
            active_index_build_id=active_index_build_id,
            source_metadata_generation=source_metadata_generation,
        ),
        retrieval_config=effective_settings.retrieval,
        job_max_attempts=effective_settings.jobs.max_attempts,
    )
