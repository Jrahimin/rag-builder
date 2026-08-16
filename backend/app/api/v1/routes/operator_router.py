"""Sanitized deployment-operator APIs (admin key required)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status

from app.core.config import LLMBackend, get_settings
from app.core.http.envelopes import ApiResponse
from app.dependencies.admin_auth import require_super_admin
from app.dependencies.operations import OperatorServiceDep
from app.dependencies.projects import (
    OperatorProjectServiceDep,
    ProjectAdministrationServiceDep,
)
from app.modules.operations.schemas.operator import (
    ActiveConfiguration,
    AuditEventResponse,
    DependencyOverview,
    MetricsSnapshot,
    OperatorOverview,
    RecentFailure,
    UsageBucket,
    UsageReport,
    UsageWorkload,
    WorkerOverview,
)
from app.modules.projects.schemas.ai_config import (
    EffectiveProjectAIConfigResponse,
    ProjectAIConfigRestore,
    ProjectAIConfigRevisionCreate,
    ProjectAIConfigRevisionResponse,
    ProjectOwnershipChange,
    ProjectOwnershipConfirm,
    ProjectOwnershipPreflight,
)
from app.modules.projects.schemas.project import (
    ProjectCreate,
    ProjectOwnershipMigrationStatus,
    ProjectResponse,
    ProjectStatusUpdate,
    ProjectUpdate,
)
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.providers.capabilities import describe_llm_capability

router = APIRouter(dependencies=[Depends(require_super_admin)])


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


@router.get("/usage", response_model=ApiResponse[UsageReport])
async def get_usage(
    service: OperatorServiceDep,
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    bucket: UsageBucket = Query(default=UsageBucket.DAY),
    organization_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None, max_length=64),
    model: str | None = Query(default=None, max_length=128),
    workload: UsageWorkload | None = Query(default=None),
) -> ApiResponse[UsageReport]:
    return ApiResponse.ok(
        await service.usage(
            start_at=start_at,
            end_at=end_at,
            bucket=bucket,
            organization_id=organization_id,
            project_id=project_id,
            provider=provider,
            model=model,
            workload=workload,
        )
    )


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
    organization_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
) -> ApiResponse[list[AuditEventResponse]]:
    return ApiResponse.ok(
        await service.audit_events(
            limit=limit,
            offset=offset,
            organization_id=organization_id,
            project_id=project_id,
        )
    )


@router.get("/provider-capabilities")
async def get_provider_capabilities(
    provider: LLMBackend | None = Query(default=None),
    model: str | None = Query(default=None),
) -> ApiResponse[list[dict[str, object]]]:
    settings = get_settings()
    providers = [provider] if provider is not None else [settings.llm.backend]
    return ApiResponse.ok(
        [
            describe_llm_capability(
                item.value,
                model or settings.llm.model,
            ).as_dict()
            for item in providers
        ]
    )


@router.get(
    "/projects",
    response_model=ApiResponse[PaginatedResult[ProjectResponse]],
)
async def list_operator_projects(
    service: OperatorProjectServiceDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=True),
    is_active: bool | None = Query(default=None),
) -> ApiResponse[PaginatedResult[ProjectResponse]]:
    page = await service.list(
        ListParams(
            limit=limit,
            offset=offset,
            include_deleted=include_deleted,
            is_active=is_active,
        )
    )
    return ApiResponse.ok(
        PaginatedResult[ProjectResponse](
            items=[ProjectResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.post(
    "/projects",
    response_model=ApiResponse[ProjectResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_operator_project(
    body: ProjectCreate,
    service: OperatorProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.create(body)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.get(
    "/projects/ownership-migration",
    response_model=ApiResponse[ProjectOwnershipMigrationStatus],
)
async def get_operator_project_ownership_migration(
    service: OperatorProjectServiceDep,
) -> ApiResponse[ProjectOwnershipMigrationStatus]:
    return ApiResponse.ok(await service.ownership_migration_status())


@router.get(
    "/projects/{project_id}",
    response_model=ApiResponse[ProjectResponse],
)
async def get_operator_project(
    project_id: uuid.UUID,
    service: OperatorProjectServiceDep,
    include_deleted: bool = Query(default=True),
) -> ApiResponse[ProjectResponse]:
    project = await service.get(project_id, include_deleted=include_deleted)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.patch(
    "/projects/{project_id}",
    response_model=ApiResponse[ProjectResponse],
)
async def update_operator_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    service: OperatorProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.update(project_id, body)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.put(
    "/projects/{project_id}/status",
    response_model=ApiResponse[ProjectResponse],
)
async def set_operator_project_status(
    project_id: uuid.UUID,
    body: ProjectStatusUpdate,
    service: OperatorProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.set_status(project_id, is_active=body.is_active)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.post(
    "/projects/{project_id}/archive",
    response_model=ApiResponse[ProjectResponse],
)
async def archive_operator_project(
    project_id: uuid.UUID,
    service: OperatorProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.soft_delete(project_id)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.post(
    "/projects/{project_id}/restore",
    response_model=ApiResponse[ProjectResponse],
)
async def restore_operator_project(
    project_id: uuid.UUID,
    service: OperatorProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.restore(project_id)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.get(
    "/projects/{project_id}/history",
    response_model=ApiResponse[list[AuditEventResponse]],
)
async def get_operator_project_history(
    project_id: uuid.UUID,
    service: OperatorServiceDep,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[list[AuditEventResponse]]:
    return ApiResponse.ok(
        await service.audit_events(
            limit=limit,
            offset=offset,
            project_id=project_id,
        )
    )


@router.get(
    "/projects/{project_id}/ai-config",
    response_model=ApiResponse[EffectiveProjectAIConfigResponse],
)
async def get_project_ai_config(
    project_id: uuid.UUID,
    service: ProjectAdministrationServiceDep,
) -> ApiResponse[EffectiveProjectAIConfigResponse]:
    resolution = await service.effective_config()
    return ApiResponse.ok(
        EffectiveProjectAIConfigResponse(
            project_id=project_id,
            active_revision_id=resolution.provenance.project_config_revision_id,
            configuration_hash=resolution.configuration_hash,
            configuration=resolution.configuration,
            origins=resolution.origins,
            provenance=resolution.provenance,
        )
    )


@router.get(
    "/projects/{project_id}/ai-config/revisions",
    response_model=ApiResponse[list[ProjectAIConfigRevisionResponse]],
)
async def list_project_ai_config_revisions(
    project_id: uuid.UUID,
    service: ProjectAdministrationServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[list[ProjectAIConfigRevisionResponse]]:
    del project_id
    rows = await service.history(limit=limit, offset=offset)
    return ApiResponse.ok([ProjectAIConfigRevisionResponse.model_validate(row) for row in rows])


@router.post(
    "/projects/{project_id}/ai-config/revisions",
    response_model=ApiResponse[ProjectAIConfigRevisionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_project_ai_config_revision(
    project_id: uuid.UUID,
    body: ProjectAIConfigRevisionCreate,
    service: ProjectAdministrationServiceDep,
) -> ApiResponse[ProjectAIConfigRevisionResponse]:
    del project_id
    row = await service.create_revision(
        body.configuration,
        expected_active_revision_id=body.expected_active_revision_id,
        reason=body.reason,
    )
    return ApiResponse.ok(ProjectAIConfigRevisionResponse.model_validate(row))


@router.post(
    "/projects/{project_id}/ai-config/revisions/{revision_id}/restore",
    response_model=ApiResponse[ProjectAIConfigRevisionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def restore_project_ai_config_revision(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    body: ProjectAIConfigRestore,
    service: ProjectAdministrationServiceDep,
) -> ApiResponse[ProjectAIConfigRevisionResponse]:
    del project_id
    row = await service.restore(
        revision_id,
        expected_active_revision_id=body.expected_active_revision_id,
        reason=body.reason,
    )
    return ApiResponse.ok(ProjectAIConfigRevisionResponse.model_validate(row))


@router.get(
    "/projects/{project_id}/ownership/preflight",
    response_model=ApiResponse[ProjectOwnershipPreflight],
)
async def get_project_ownership_preflight(
    project_id: uuid.UUID,
    target_organization_id: uuid.UUID,
    service: ProjectAdministrationServiceDep,
) -> ApiResponse[ProjectOwnershipPreflight]:
    del project_id
    return ApiResponse.ok(await service.ownership_preflight(target_organization_id))


@router.post(
    "/projects/{project_id}/ownership/reassign",
    response_model=ApiResponse[ProjectResponse],
)
async def reassign_project_ownership(
    project_id: uuid.UUID,
    body: ProjectOwnershipChange,
    service: ProjectAdministrationServiceDep,
) -> ApiResponse[ProjectResponse]:
    del project_id
    project = await service.reassign_ownership(
        expected_current_organization_id=body.expected_current_organization_id,
        target_organization_id=body.target_organization_id,
        reason=body.reason,
    )
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.post(
    "/projects/{project_id}/ownership/confirm",
    response_model=ApiResponse[ProjectResponse],
)
async def confirm_project_ownership(
    project_id: uuid.UUID,
    body: ProjectOwnershipConfirm,
    service: ProjectAdministrationServiceDep,
) -> ApiResponse[ProjectResponse]:
    del project_id
    project = await service.confirm_ownership(
        expected_current_organization_id=body.expected_current_organization_id,
        reason=body.reason,
    )
    return ApiResponse.ok(ProjectResponse.model_validate(project))
