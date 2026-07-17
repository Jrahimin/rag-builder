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
    "/health",
    response_model=ApiResponse[LivenessStatus],
    summary="Liveness probe",
    description="Returns 200 while the process is running. Does not touch dependencies.",
)
async def health(service: HealthServiceDep) -> ApiResponse[LivenessStatus]:
    return ApiResponse.ok(service.liveness())


@router.get(
    "/ready",
    response_model=ApiResponse[ReadinessStatus],
    summary="Readiness probe",
    description=(
        "Probes PostgreSQL (including pgvector), Redis and MinIO. Returns 200 when every "
        "dependency is reachable, otherwise 503."
    ),
)
async def ready(service: HealthServiceDep, response: Response) -> ApiResponse[ReadinessStatus]:
    result = await service.readiness()
    if result.status != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ApiResponse.ok(result)
