"""Operator-owned Project AI policy and legacy ownership administration."""

from __future__ import annotations

import uuid
from typing import overload

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.generation import Generation
from app.models.index_build import IndexBuild
from app.models.job_run import JobRun
from app.models.organization import Organization
from app.models.project import Project
from app.models.project_ai_config_revision import ProjectAIConfigRevision
from app.models.source_metadata import SourceActivationEvent, SourceMetadataRevision
from app.modules.projects.repositories.project_ai_config_repository import (
    ProjectAIConfigRepository,
)
from app.modules.projects.schemas.ai_config import ProjectOwnershipPreflight
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)
from app.platform.auth.contracts import AuthEventPublisher
from app.platform.auth.events import OrganizationAuthInvalidated
from app.platform.config.profiles import execution_profile, profile_hash
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    EffectiveConfigResolution,
    ProjectAIConfig,
    V1NormalizationResult,
    V2ProfileNormalizationResult,
    normalize_v1_project_config,
    normalize_v2_project_config,
    resolve_project_ai_config,
    stable_hash,
)


class ProjectAdministrationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        project_id: uuid.UUID,
        repository: ProjectAIConfigRepository,
        settings: Settings,
        audit: AuditRecorder,
        actor_id: str,
        auth_events: AuthEventPublisher | None = None,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._repository = repository
        self._settings = settings
        self._audit = audit
        self._actor_id = actor_id
        self._auth_events = auth_events

    async def effective_config(self) -> EffectiveConfigResolution:
        await self._require_project()
        active = await self._repository.get_active()
        return resolve_project_ai_config(self._settings, _record(active))

    async def history(self, *, limit: int, offset: int) -> list[ProjectAIConfigRevision]:
        await self._require_project()
        return await self._repository.list(limit=limit, offset=offset)

    async def create_revision(
        self,
        configuration: ProjectAIConfig,
        *,
        expected_active_revision_id: uuid.UUID | None,
        reason: str,
        restored_from_revision_id: uuid.UUID | None = None,
        source: str = "operator_write",
    ) -> ProjectAIConfigRevision:
        project = await self._require_project()
        if project.active_ai_config_revision_id != expected_active_revision_id:
            raise ConflictError(
                message="The active Project configuration changed.",
                code="project_config_revision_conflict",
                context={
                    "expected": str(expected_active_revision_id),
                    "actual": str(project.active_ai_config_revision_id),
                },
            )
        if configuration.execution.profile_id is not None:
            selected_profile = execution_profile(configuration.execution.profile_id)
            selected_hash = profile_hash(selected_profile)
            if (
                configuration.execution.profile_hash is not None
                and configuration.execution.profile_hash != selected_hash
            ):
                raise BadRequestError(
                    message="The pinned RAG execution profile definition no longer matches.",
                    code="execution_profile_hash_mismatch",
                    context={
                        "profile_id": configuration.execution.profile_id,
                        "stored_hash": configuration.execution.profile_hash,
                        "current_hash": selected_hash,
                    },
                )
            configuration = configuration.model_copy(
                update={
                    "execution": configuration.execution.model_copy(
                        update={"profile_hash": selected_hash}
                    )
                }
            )
        payload = configuration.model_dump(mode="json", exclude_none=True)
        # Resolve before persistence so unsupported provider/model parameters never activate.
        candidate = ConfigRevisionRecord(
            id=uuid.uuid4(),
            revision_number=await self._repository.next_revision_number(),
            configuration_hash=stable_hash(payload),
            configuration=payload,
            schema_version=2,
        )
        resolve_project_ai_config(self._settings, candidate)
        revision = ProjectAIConfigRevision(
            id=candidate.id,
            project_id=self._project_id,
            revision_number=candidate.revision_number,
            schema_version=2,
            configuration_hash=candidate.configuration_hash,
            configuration=payload,
            created_by=self._actor_id,
            source=source,
            reason=reason.strip(),
            restored_from_revision_id=restored_from_revision_id,
        )
        self._repository.add(revision)
        await self._session.flush()
        project.active_ai_config_revision_id = revision.id
        self._audit.record(
            event_type=(
                AuditEventType.PROJECT_CONFIG_REVISION_RESTORED
                if restored_from_revision_id is not None
                else AuditEventType.PROJECT_CONFIG_REVISION_CREATED
            ),
            actor_type=AuditActorType.OPERATOR,
            actor_id=self._actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="project_ai_config_revision",
            resource_id=revision.id,
            outcome=AuditOutcome.SUCCESS,
            detail={
                "revision_number": revision.revision_number,
                "configuration_hash": revision.configuration_hash,
                "reason": reason.strip(),
                "schema_version": 2,
                "source": source,
                "restored_from_revision_id": (
                    str(restored_from_revision_id) if restored_from_revision_id else None
                ),
            },
        )
        await self._session.commit()
        await self._session.refresh(revision)
        return revision

    async def restore(
        self,
        revision_id: uuid.UUID,
        *,
        expected_active_revision_id: uuid.UUID | None,
        reason: str,
    ) -> ProjectAIConfigRevision:
        source = await self._repository.get(revision_id)
        if source is None:
            raise NotFoundError(
                message="Project configuration revision not found.",
                code="project_config_revision_not_found",
            )
        configuration = (
            normalize_v1_project_config(self._settings, _record(source)).configuration
            if source.schema_version == 1
            else ProjectAIConfig.model_validate(source.configuration)
        )
        return await self.create_revision(
            configuration,
            expected_active_revision_id=expected_active_revision_id,
            reason=reason,
            restored_from_revision_id=source.id,
            source="restore_normalized_v1" if source.schema_version == 1 else "restore_v2",
        )

    async def normalization_preview(self) -> tuple[ProjectAIConfigRevision, V1NormalizationResult]:
        await self._require_project()
        active = await self._repository.get_active()
        if active is None:
            raise NotFoundError(
                message="The Project has no active V1 configuration revision.",
                code="active_project_config_not_found",
            )
        result = normalize_v1_project_config(self._settings, _record(active))
        return active, result

    async def normalize_active_v1(
        self,
        *,
        expected_active_revision_id: uuid.UUID,
        reason: str,
    ) -> ProjectAIConfigRevision:
        active, preview = await self.normalization_preview()
        if active.id != expected_active_revision_id:
            raise ConflictError(
                message="The active Project configuration changed.",
                code="project_config_revision_conflict",
                context={
                    "expected": str(expected_active_revision_id),
                    "actual": str(active.id),
                },
            )
        return await self.create_revision(
            preview.configuration,
            expected_active_revision_id=expected_active_revision_id,
            reason=reason,
            restored_from_revision_id=active.id,
            source="super_admin_v1_normalization",
        )

    async def profile_normalization_preview(
        self,
    ) -> tuple[ProjectAIConfigRevision, V2ProfileNormalizationResult]:
        await self._require_project()
        active = await self._repository.get_active()
        if active is None or active.schema_version != 2:
            raise NotFoundError(
                message="The Project has no active V2 configuration revision.",
                code="active_v2_project_config_not_found",
            )
        return active, normalize_v2_project_config(self._settings, _record(active))

    async def normalize_active_v2_profile(
        self,
        *,
        expected_active_revision_id: uuid.UUID,
        reason: str,
    ) -> ProjectAIConfigRevision:
        active, preview = await self.profile_normalization_preview()
        if active.id != expected_active_revision_id:
            raise ConflictError(
                message="The active Project configuration changed.",
                code="project_config_revision_conflict",
                context={"expected": str(expected_active_revision_id), "actual": str(active.id)},
            )
        payload = preview.configuration.model_dump(mode="json", exclude_none=True)
        if stable_hash(payload) == active.configuration_hash:
            return active
        return await self.create_revision(
            preview.configuration,
            expected_active_revision_id=expected_active_revision_id,
            reason=reason,
            restored_from_revision_id=active.id,
            source="super_admin_v2_profile_normalization",
        )

    async def ownership_preflight(
        self, target_organization_id: uuid.UUID
    ) -> ProjectOwnershipPreflight:
        project = await self._require_project()
        await self._require_organization(target_organization_id)
        counts: dict[str, int] = {}
        for name, model in (
            ("documents", Document),
            ("conversations", Conversation),
            ("jobs", JobRun),
            ("generations", Generation),
            ("index_builds", IndexBuild),
            ("source_revisions", SourceMetadataRevision),
            ("source_activations", SourceActivationEvent),
        ):
            counts[name] = int(
                await self._session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.project_id == self._project_id)
                )
                or 0
            )
        return ProjectOwnershipPreflight(
            project_id=project.id,
            current_organization_id=project.organization_id,
            target_organization_id=target_organization_id,
            ownership_locked=project.ownership_locked,
            can_reassign=not project.ownership_locked,
            resource_counts=counts,
        )

    async def reassign_ownership(
        self,
        *,
        expected_current_organization_id: uuid.UUID,
        target_organization_id: uuid.UUID,
        reason: str,
    ) -> Project:
        project = await self._require_unlocked_project()
        if project.organization_id != expected_current_organization_id:
            raise ConflictError(
                message="The Project organization changed.",
                code="project_ownership_conflict",
            )
        await self._require_organization(target_organization_id)
        old_organization_id = project.organization_id
        project.organization_id = target_organization_id
        project.ownership_locked = True
        self._record_ownership_event(
            project,
            AuditEventType.PROJECT_OWNERSHIP_REASSIGNED,
            old_organization_id=old_organization_id,
            reason=reason,
        )
        await self._session.commit()
        await self._session.refresh(project)
        if self._auth_events is not None:
            await self._auth_events.publish(OrganizationAuthInvalidated(old_organization_id))
            await self._auth_events.publish(OrganizationAuthInvalidated(target_organization_id))
        return project

    async def confirm_ownership(
        self,
        *,
        expected_current_organization_id: uuid.UUID,
        reason: str,
    ) -> Project:
        project = await self._require_unlocked_project()
        if project.organization_id != expected_current_organization_id:
            raise ConflictError(
                message="The Project organization changed.",
                code="project_ownership_conflict",
            )
        project.ownership_locked = True
        self._record_ownership_event(
            project,
            AuditEventType.PROJECT_OWNERSHIP_CONFIRMED,
            old_organization_id=project.organization_id,
            reason=reason,
        )
        await self._session.commit()
        await self._session.refresh(project)
        if self._auth_events is not None:
            await self._auth_events.publish(OrganizationAuthInvalidated(project.organization_id))
        return project

    async def _require_project(self) -> Project:
        project = await self._repository.lock_project()
        if project is None:
            raise NotFoundError(message="Project not found.", code="project_not_found")
        return project

    async def _require_unlocked_project(self) -> Project:
        project = await self._require_project()
        if project.ownership_locked:
            raise ConflictError(
                message="Project ownership is locked.",
                code="project_ownership_locked",
            )
        return project

    async def _require_organization(self, organization_id: uuid.UUID) -> None:
        exists = await self._session.scalar(
            select(Organization.id).where(
                Organization.id == organization_id,
                Organization.deleted_at.is_(None),
                Organization.is_active.is_(True),
            )
        )
        if exists is None:
            raise NotFoundError(message="Organization not found.", code="organization_not_found")

    def _record_ownership_event(
        self,
        project: Project,
        event_type: AuditEventType,
        *,
        old_organization_id: uuid.UUID,
        reason: str,
    ) -> None:
        self._audit.record(
            event_type=event_type,
            actor_type=AuditActorType.OPERATOR,
            actor_id=self._actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            outcome=AuditOutcome.SUCCESS,
            detail={
                "previous_organization_id": str(old_organization_id),
                "target_organization_id": str(project.organization_id),
                "ownership_locked": True,
                "reason": reason.strip(),
            },
        )


@overload
def _record(revision: ProjectAIConfigRevision) -> ConfigRevisionRecord: ...


@overload
def _record(revision: None) -> None: ...


def _record(revision: ProjectAIConfigRevision | None) -> ConfigRevisionRecord | None:
    if revision is None:
        return None
    return ConfigRevisionRecord(
        id=revision.id,
        revision_number=revision.revision_number,
        configuration_hash=revision.configuration_hash,
        configuration=dict(revision.configuration),
        schema_version=revision.schema_version,
    )
