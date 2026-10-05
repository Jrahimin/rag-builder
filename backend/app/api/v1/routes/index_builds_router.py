"""Guarded project corpus and immutable index lifecycle routes."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.composition.acceptance import observed_acceptance_service
from app.composition.audit import DatabaseAuditRecorder
from app.core.http.envelopes import ApiResponse
from app.dependencies.admin_auth import CurrentAdminDep, require_super_admin
from app.dependencies.common import DbSessionDep
from app.dependencies.retrieval import IndexLifecycleServiceDep
from app.modules.knowledge.schemas.scope_review import ScopeReviewCreate, ScopeReviewResponse
from app.modules.knowledge.services.scope_review_service import ScopeReviewService
from app.modules.retrieval.build_acceptance import (
    AcceptanceSetDefinition,
    BuildAcceptanceArtifact,
    BuildAcceptanceResponse,
)
from app.modules.retrieval.schemas.acceptance_report import (
    ObservedAcceptanceReportCreate,
    ObservedAcceptanceReportResponse,
)
from app.modules.retrieval.schemas.index_lifecycle import (
    IndexBuildListResponse,
    IndexBuildResponse,
    LifecycleJobResponse,
)
from app.modules.retrieval.services.build_acceptance_service import BuildAcceptanceService
from app.modules.retrieval.services.index_build_read_service import IndexBuildReadService

router = APIRouter()


@router.get("", response_model=ApiResponse[IndexBuildListResponse])
async def list_index_builds(
    project_id: uuid.UUID, session: DbSessionDep
) -> ApiResponse[IndexBuildListResponse]:
    return ApiResponse.ok(await IndexBuildReadService(session, project_id).list())


@router.post(
    "/reembed",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reembed_corpus(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_reembed())


@router.post(
    "/reindex",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_corpus(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_reindex())


@router.post(
    "/reprocess-private",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_private_corpus(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_private_reprocess())


@router.post(
    "/{build_id}/revalidate-private",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def revalidate_private_corpus(
    project_id: uuid.UUID, build_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_private_reprocess(source_build_id=build_id))


@router.post(
    "/reconcile-storage",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reconcile_storage(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_storage_reconciliation())


@router.post("/{build_id}/activate", response_model=ApiResponse[IndexBuildResponse])
async def activate_index_build(
    project_id: uuid.UUID,
    build_id: uuid.UUID,
    service: IndexLifecycleServiceDep,
) -> ApiResponse[IndexBuildResponse]:
    del project_id
    return ApiResponse.ok(IndexBuildResponse.model_validate(await service.activate(build_id)))


@router.post("/rollback", response_model=ApiResponse[IndexBuildResponse])
async def rollback_index_build(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[IndexBuildResponse]:
    del project_id
    return ApiResponse.ok(IndexBuildResponse.model_validate(await service.rollback()))


@router.get(
    "/{build_id}/acceptance",
    response_model=ApiResponse[list[BuildAcceptanceResponse]],
    dependencies=[Depends(require_super_admin)],
)
async def list_build_acceptance(
    project_id: uuid.UUID, build_id: uuid.UUID, session: DbSessionDep
) -> ApiResponse[list[BuildAcceptanceResponse]]:
    rows = await BuildAcceptanceService(session, project_id).list(build_id)
    return ApiResponse.ok([BuildAcceptanceResponse.model_validate(row) for row in rows])


@router.post(
    "/{build_id}/acceptance",
    response_model=ApiResponse[BuildAcceptanceResponse],
    dependencies=[Depends(require_super_admin)],
)
async def attest_build_acceptance(
    project_id: uuid.UUID,
    build_id: uuid.UUID,
    artifact: BuildAcceptanceArtifact,
    session: DbSessionDep,
    admin: CurrentAdminDep,
) -> ApiResponse[BuildAcceptanceResponse]:
    row = await BuildAcceptanceService(session, project_id).attest(
        build_id, artifact, actor_id=str(admin.id), audit=DatabaseAuditRecorder(session, project_id)
    )
    return ApiResponse.ok(BuildAcceptanceResponse.model_validate(row))


@router.post(
    "/{build_id}/acceptance-set",
    response_model=ApiResponse[dict[str, Any]],
    dependencies=[Depends(require_super_admin)],
)
async def define_acceptance_set(
    project_id: uuid.UUID,
    build_id: uuid.UUID,
    body: AcceptanceSetDefinition,
    admin: CurrentAdminDep,
    session: DbSessionDep,
) -> ApiResponse[dict[str, Any]]:
    row = await BuildAcceptanceService(session, project_id).define(
        build_id, body, actor_id=str(admin.id), audit=DatabaseAuditRecorder(session, project_id)
    )
    return ApiResponse.ok(
        {"id": row.id, "definition_hash": row.definition_hash, "definition": row.definition}
    )


@router.post(
    "/{build_id}/scope-reviews",
    response_model=ApiResponse[ScopeReviewResponse],
    dependencies=[Depends(require_super_admin)],
)
async def publish_scope_review(
    project_id: uuid.UUID,
    build_id: uuid.UUID,
    body: ScopeReviewCreate,
    admin: CurrentAdminDep,
    session: DbSessionDep,
) -> ApiResponse[ScopeReviewResponse]:
    row = await ScopeReviewService(session, project_id).publish(
        build_id, body, actor_id=str(admin.id), audit=DatabaseAuditRecorder(session, project_id)
    )
    return ApiResponse.ok(ScopeReviewResponse.model_validate(row))


@router.post(
    "/{build_id}/acceptance-reports",
    response_model=ApiResponse[ObservedAcceptanceReportResponse],
    dependencies=[Depends(require_super_admin)],
)
async def publish_observed_acceptance(
    project_id: uuid.UUID,
    build_id: uuid.UUID,
    body: ObservedAcceptanceReportCreate,
    admin: CurrentAdminDep,
    session: DbSessionDep,
) -> ApiResponse[ObservedAcceptanceReportResponse]:
    row = await observed_acceptance_service(session, project_id).publish(
        build_id, body, actor_id=str(admin.id), audit=DatabaseAuditRecorder(session, project_id)
    )
    return ApiResponse.ok(ObservedAcceptanceReportResponse.model_validate(row))


@router.get(
    "/{build_id}/acceptance-identity",
    response_model=ApiResponse[dict[str, Any]],
    dependencies=[Depends(require_super_admin)],
)
async def build_acceptance_identity(
    project_id: uuid.UUID, build_id: uuid.UUID, session: DbSessionDep, admin: CurrentAdminDep
) -> ApiResponse[dict[str, Any]]:
    result = await BuildAcceptanceService(session, project_id).identity(build_id)
    from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome

    DatabaseAuditRecorder(session, project_id).record(
        event_type=AuditEventType.INDEX_BUILD_ACCEPTED,
        actor_type=AuditActorType.OPERATOR,
        actor_id=str(admin.id),
        resource_type="index_acceptance_identity",
        resource_id=build_id,
        outcome=AuditOutcome.SUCCESS,
    )
    await session.commit()
    return ApiResponse.ok(result)
