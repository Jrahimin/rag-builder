"""System health endpoints (mounted at the application root, unversioned).

These are infrastructure probes (Docker, Kubernetes, load balancers) and are
deliberately *not* placed under ``/api/v1`` so their contract stays stable
across API versions.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.common import HealthServiceDep
from app.platform.system.schemas import LivenessStatus, ReadinessStatus

router = APIRouter(tags=["system"])


@router.get(
    "/health/live",
    response_model=ApiResponse[LivenessStatus],
    summary="Liveness probe",
    description="Returns 200 while the process is running. Does not touch dependencies.",
)
async def live(service: HealthServiceDep) -> ApiResponse[LivenessStatus]:
    return ApiResponse.ok(service.liveness())


@router.get(
    "/health/ready",
    response_model=ApiResponse[ReadinessStatus],
    summary="Readiness probe",
    description=(
        "Probes PostgreSQL (including pgvector), Redis, and object storage, then includes "
        "cached startup provider capability results. Returns 200 when every required "
        "dependency is healthy, otherwise 503."
    ),
)
async def ready(service: HealthServiceDep, response: Response) -> ApiResponse[ReadinessStatus]:
    result = await service.readiness()
    if result.status != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ApiResponse.ok(result)


@router.get("/health", include_in_schema=False, deprecated=True)
async def legacy_live(service: HealthServiceDep) -> ApiResponse[LivenessStatus]:
    """Backward-compatible alias; use ``/health/live`` for new integrations."""
    return await live(service)


@router.get("/ready", include_in_schema=False, deprecated=True)
async def legacy_ready(
    service: HealthServiceDep,
    response: Response,
) -> ApiResponse[ReadinessStatus]:
    """Backward-compatible alias; use ``/health/ready`` for new integrations."""
    return await ready(service, response)
