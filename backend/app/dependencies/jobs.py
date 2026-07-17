"""FastAPI dependency wiring for durable jobs and executor transport."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.jobs import build_job_service
from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.modules.jobs.services.job_service import JobService
from app.platform.jobs.contracts import DurableJobSubmitter, JobQueue
from app.platform.jobs.implementations.job_queue_factory import get_job_queue


def get_job_queue_dep() -> JobQueue:
    return get_job_queue()


def get_job_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    queue: Annotated[JobQueue, Depends(get_job_queue_dep)],
) -> JobService:
    return build_job_service(
        session=session,
        project_id=project_id,
        settings=get_settings(),
        queue=queue,
    )


def get_job_submitter(
    service: Annotated[JobService, Depends(get_job_service)],
) -> DurableJobSubmitter:
    return service


JobServiceDep = Annotated[JobService, Depends(get_job_service)]
JobSubmitterDep = Annotated[DurableJobSubmitter, Depends(get_job_submitter)]
