"""Project-scoped durable job inspection and retry routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.jobs import JobServiceDep
from app.models.job_run import JobState, JobType
from app.modules.jobs.schemas.job import (
    JobDetailResponse,
    JobListParams,
    JobResponse,
)
from app.platform.http.pagination import PaginatedResult

router = APIRouter()


@router.get(
    "",
    response_model=ApiResponse[PaginatedResult[JobResponse]],
    summary="List durable jobs in a project",
)
async def list_jobs(
    project_id: uuid.UUID,
    service: JobServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    state: JobState | None = Query(default=None),
    job_type: JobType | None = Query(default=None),
    document_id: uuid.UUID | None = Query(default=None),
) -> ApiResponse[PaginatedResult[JobResponse]]:
    del project_id
    page = await service.list(
        JobListParams(
            limit=limit,
            offset=offset,
            state=state,
            job_type=job_type,
            document_id=document_id,
        )
    )
    return ApiResponse.ok(
        PaginatedResult[JobResponse](
            items=[JobResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.get(
    "/{job_id}",
    response_model=ApiResponse[JobDetailResponse],
    summary="Get durable job detail",
)
async def get_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    service: JobServiceDep,
) -> ApiResponse[JobDetailResponse]:
    del project_id
    detail = await service.get_detail(job_id)
    base = JobResponse.model_validate(detail.run).model_dump()
    return ApiResponse.ok(
        JobDetailResponse.model_validate(
            {
                **base,
                "payload": detail.run.payload,
                "configuration_hash": detail.configuration.configuration_hash,
                "configuration_schema_version": detail.configuration.schema_version,
                "configuration": detail.configuration.configuration,
            }
        )
    )


@router.post(
    "/{job_id}/retry",
    response_model=ApiResponse[JobResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed durable job",
)
async def retry_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    service: JobServiceDep,
) -> ApiResponse[JobResponse]:
    del project_id
    run = await service.retry(job_id)
    return ApiResponse.ok(JobResponse.model_validate(run))
