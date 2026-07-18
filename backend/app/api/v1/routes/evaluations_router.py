"""Project-scoped quality dataset and evaluation run APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.evaluation import EvaluationServiceDep
from app.modules.evaluation.schemas.evaluation import (
    EvaluationDatasetCreate,
    EvaluationDatasetResponse,
    EvaluationRunCreate,
    EvaluationRunResponse,
    QualitySummary,
)

router = APIRouter()


@router.post(
    "/datasets",
    response_model=ApiResponse[EvaluationDatasetResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create an immutable evaluation dataset version",
)
async def create_dataset(
    project_id: uuid.UUID,
    body: EvaluationDatasetCreate,
    service: EvaluationServiceDep,
) -> ApiResponse[EvaluationDatasetResponse]:
    del project_id
    dataset = await service.create_dataset(body)
    return ApiResponse.ok(EvaluationDatasetResponse.model_validate(dataset))


@router.get(
    "/datasets",
    response_model=ApiResponse[list[EvaluationDatasetResponse]],
    summary="List evaluation dataset versions",
)
async def list_datasets(
    project_id: uuid.UUID,
    service: EvaluationServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[list[EvaluationDatasetResponse]]:
    del project_id
    datasets = await service.list_datasets(limit=limit, offset=offset)
    return ApiResponse.ok(
        [EvaluationDatasetResponse.model_validate(dataset) for dataset in datasets]
    )


@router.post(
    "/runs",
    response_model=ApiResponse[EvaluationRunResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a reproducible quality run",
)
async def create_run(
    project_id: uuid.UUID,
    body: EvaluationRunCreate,
    service: EvaluationServiceDep,
) -> ApiResponse[EvaluationRunResponse]:
    del project_id
    run, _submission = await service.queue_run(body)
    return ApiResponse.ok(run)


@router.get(
    "/runs",
    response_model=ApiResponse[list[EvaluationRunResponse]],
    summary="List quality runs",
)
async def list_runs(
    project_id: uuid.UUID,
    service: EvaluationServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[list[EvaluationRunResponse]]:
    del project_id
    return ApiResponse.ok(await service.list_runs(limit=limit, offset=offset))


@router.get(
    "/runs/{run_id}",
    response_model=ApiResponse[EvaluationRunResponse],
    summary="Get one quality run and its exact versions",
)
async def get_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: EvaluationServiceDep,
) -> ApiResponse[EvaluationRunResponse]:
    del project_id
    return ApiResponse.ok(await service.get_run(run_id))


@router.get(
    "/quality",
    response_model=ApiResponse[QualitySummary],
    summary="Get the latest dataset, run, metrics, and acceptance thresholds",
)
async def get_quality(
    project_id: uuid.UUID,
    service: EvaluationServiceDep,
) -> ApiResponse[QualitySummary]:
    del project_id
    return ApiResponse.ok(await service.quality_summary())
