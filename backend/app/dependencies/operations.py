"""FastAPI composition for the deployment operator backend."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.dependencies.common import DbSessionDep, get_health_service, get_redis_connectivity
from app.modules.operations.repositories.operator_repository import OperatorRepository
from app.modules.operations.services.operator_service import OperatorService
from app.platform.jobs.worker_registry import WorkerRegistry


def get_operator_service(request: Request, session: DbSessionDep) -> OperatorService:
    settings = request.app.state.settings
    redis = get_redis_connectivity(request)
    return OperatorService(
        settings=settings,
        repository=OperatorRepository(session),
        health=get_health_service(request),
        worker_registry=WorkerRegistry(redis.client, settings),
        preflight=request.app.state.preflight,
    )


OperatorServiceDep = Annotated[OperatorService, Depends(get_operator_service)]
