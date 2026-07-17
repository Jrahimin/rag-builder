"""Sanitized deployment-operator APIs (admin key required)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.http.envelopes import ApiResponse
from app.dependencies.auth import require_admin_api_key
from app.dependencies.operations import OperatorServiceDep
from app.modules.operations.schemas.operator import (
    ActiveConfiguration,
    AuditEventResponse,
    DependencyOverview,
    MetricsSnapshot,
    OperatorOverview,
    RecentFailure,
    WorkerOverview,
)

router = APIRouter(dependencies=[Depends(require_admin_api_key)])


@router.get("/overview", response_model=ApiResponse[OperatorOverview])
async def get_overview(service: OperatorServiceDep) -> ApiResponse[OperatorOverview]:
    return ApiResponse.ok(await service.overview())


@router.get("/dependencies", response_model=ApiResponse[DependencyOverview])
async def get_dependencies(service: OperatorServiceDep) -> ApiResponse[DependencyOverview]:
    return ApiResponse.ok(await service.dependencies())


@router.get("/workers", response_model=ApiResponse[WorkerOverview])
async def get_workers(service: OperatorServiceDep) -> ApiResponse[WorkerOverview]:
    return ApiResponse.ok(await service.workers())


@router.get("/metrics", response_model=ApiResponse[MetricsSnapshot])
async def get_metrics(service: OperatorServiceDep) -> ApiResponse[MetricsSnapshot]:
    return ApiResponse.ok(await service.metrics())


@router.get("/configuration", response_model=ApiResponse[ActiveConfiguration])
async def get_configuration(service: OperatorServiceDep) -> ApiResponse[ActiveConfiguration]:
    return ApiResponse.ok(await service.active_configuration())


@router.get("/failures", response_model=ApiResponse[list[RecentFailure]])
async def get_recent_failures(
    service: OperatorServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[list[RecentFailure]]:
    return ApiResponse.ok(await service.recent_failures(limit=limit))


@router.get("/audit-events", response_model=ApiResponse[list[AuditEventResponse]])
async def get_audit_events(
    service: OperatorServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[list[AuditEventResponse]]:
    return ApiResponse.ok(await service.audit_events(limit=limit, offset=offset))
