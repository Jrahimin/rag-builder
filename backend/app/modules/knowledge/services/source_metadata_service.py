"""Immutable source metadata lifecycle owned by the Knowledge module."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.document import Document
from app.models.project import Project
from app.models.source_metadata import (
    SourceActivationEvent,
    SourceGroup,
    SourceLifecycleStatus,
    SourceMetadataRevision,
    SourceRelationshipType,
    SourceRevisionRelationship,
    SourceRole,
)
from app.modules.knowledge.repositories.source_metadata_repository import (
    SourceMetadataRepository,
)
from app.modules.knowledge.schemas.source_metadata import (
    ActiveSourceResponse,
    SourceActivationResponse,
    SourceRelationshipResponse,
    SourceRevisionCreate,
    SourceRevisionCreateResponse,
    SourceRevisionResponse,
    SourceStateResponse,
)
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)

_PROJECT_NOT_FOUND = {"message": "Project not found.", "code": "project_not_found"}
_DOCUMENT_NOT_FOUND = {"message": "Document not found.", "code": "document_not_found"}
_REVISION_NOT_FOUND = {
    "message": "Source metadata revision not found.",
    "code": "source_revision_not_found",
}


class SourceMetadataService:
    """Validates, persists, activates, and resolves source metadata revisions."""

    def __init__(
        self,
        session: AsyncSession,
        repository: SourceMetadataRepository,
        *,
        audit: AuditRecorder | None = None,
        actor_id: str | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit
        self._actor_id = actor_id or "system"

    async def initialize_document(
        self,
        document: Document,
        metadata: SourceRevisionCreate | None,
    ) -> tuple[SourceMetadataRevision, SourceActivationEvent]:
        """Stage a first revision during upload; the caller owns the transaction."""
        project = await self._require_locked_project()
        existing = await self._repository.latest_for_document(document.id)
        if existing is not None:
            return existing[1], existing[0]

        data = metadata or SourceRevisionCreate(
            revision_label="Initial",
            title=document.filename,
            lifecycle_status=SourceLifecycleStatus.UNSPECIFIED,
            source_role=SourceRole.UNSPECIFIED,
            change_reason="Initial neutral source metadata",
            activate=True,
        )
        revision = await self._stage_revision(document, data)
        activation = await self._stage_activation(
            project,
            revision,
            reason=data.change_reason or "Source metadata created",
        )
        return revision, activation

    async def create_revision(
        self,
        document_id: uuid.UUID,
        data: SourceRevisionCreate,
    ) -> SourceRevisionCreateResponse:
        project = await self._require_locked_project()
        document = await self._require_document(document_id)
        revision = await self._stage_revision(document, data)
        activation: SourceActivationEvent | None = None
        if data.activate:
            activation = await self._stage_activation(
                project,
                revision,
                reason=data.change_reason or "Source metadata created",
            )
        await self._session.commit()
        await self._session.refresh(revision)
        if activation is not None:
            await self._session.refresh(activation)
        return SourceRevisionCreateResponse(
            revision=await self._revision_response(revision),
            activation=(
                SourceActivationResponse.model_validate(activation)
                if activation is not None
                else None
            ),
        )

    async def activate(
        self,
        revision_id: uuid.UUID,
        *,
        reason: str,
    ) -> SourceActivationEvent:
        project = await self._require_locked_project()
        revision = await self._require_revision(revision_id)
        latest = await self._repository.latest_for_document(revision.document_id)
        if latest is not None and latest[1].id == revision.id:
            return latest[0]
        activation = await self._stage_activation(project, revision, reason=reason)
        await self._session.commit()
        await self._session.refresh(activation)
        return activation

    async def get_revision(self, revision_id: uuid.UUID) -> SourceRevisionResponse:
        return await self._revision_response(await self._require_revision(revision_id))

    async def active_for_document(self, document_id: uuid.UUID) -> ActiveSourceResponse:
        await self._require_document(document_id)
        latest = await self._repository.latest_for_document(document_id)
        if latest is None:
            raise NotFoundError(
                message="Active source metadata was not found for this Document.",
                code="source_metadata_not_found",
            )
        activation, revision = latest
        return ActiveSourceResponse(
            document_id=document_id,
            activation=SourceActivationResponse.model_validate(activation),
            revision=await self._revision_response(revision),
        )

    async def history(
        self,
        document_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[SourceRevisionResponse]:
        await self._require_document(document_id)
        rows = await self._repository.list_document_revisions(
            document_id, limit=limit, offset=offset
        )
        relationships, overlaps = await self._response_context(rows)
        return [
            await self._revision_response(
                row,
                relationships=relationships,
                overlaps=overlaps,
            )
            for row in rows
        ]

    async def activation_history(
        self,
        *,
        document_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> list[SourceActivationEvent]:
        if document_id is not None:
            await self._require_document(document_id)
        return await self._repository.list_activations(
            document_id=document_id, limit=limit, offset=offset
        )

    async def state(self, generation: int | None = None) -> SourceStateResponse:
        project = await self._require_project()
        resolved = project.source_metadata_generation if generation is None else generation
        if resolved < 0 or resolved > project.source_metadata_generation:
            raise BadRequestError(
                message="generation must be between 0 and the current Project generation.",
                code="source_generation_invalid",
            )
        rows = await self._repository.state_at(resolved)
        revisions = [revision for _, revision in rows]
        relationships, overlaps = await self._response_context(revisions)
        return SourceStateResponse(
            project_id=project.id,
            generation=resolved,
            current_generation=project.source_metadata_generation,
            items=[
                ActiveSourceResponse(
                    document_id=revision.document_id,
                    activation=SourceActivationResponse.model_validate(activation),
                    revision=await self._revision_response(
                        revision,
                        relationships=relationships,
                        overlaps=overlaps,
                    ),
                )
                for activation, revision in rows
            ],
        )

    async def _stage_revision(
        self,
        document: Document,
        data: SourceRevisionCreate,
    ) -> SourceMetadataRevision:
        active = await self._repository.latest_for_document(document.id)
        group: SourceGroup | None = None
        if data.source_group_id is not None:
            group = await self._repository.get_group(data.source_group_id)
            if group is None:
                raise NotFoundError(
                    message="Source group not found in this Project.",
                    code="source_group_not_found",
                )
        elif active is not None and not data.create_new_group:
            group = await self._repository.get_group(active[1].source_group_id)
        if group is None:
            group = SourceGroup(
                id=uuid.uuid4(),
                project_id=self._repository.project_id,
                created_by=self._actor_id,
            )
            self._repository.add(group)
            await self._repository.flush()

        next_number = await self._repository.next_revision_number(group.id)
        if data.revision_number is not None and data.revision_number != next_number:
            raise ConflictError(
                message=f"The next revision number for this source group is {next_number}.",
                code="source_revision_order_conflict",
            )

        revision = SourceMetadataRevision(
            id=uuid.uuid4(),
            project_id=self._repository.project_id,
            document_id=document.id,
            source_group_id=group.id,
            revision_number=next_number,
            revision_label=data.revision_label,
            title=data.title or document.filename,
            source_type=data.source_type,
            published_date=data.published_date,
            effective_from=data.effective_from,
            effective_to=data.effective_to,
            lifecycle_status=data.lifecycle_status,
            source_role=data.source_role,
            change_reason=data.change_reason or "Source metadata created",
            created_by=self._actor_id,
            content_hash=document.content_sha256,
        )
        self._repository.add(revision)
        await self._repository.flush()

        for relation in data.relationships:
            target = await self._repository.get_revision(relation.target_revision_id)
            if target is None:
                raise NotFoundError(
                    message="Relationship target revision not found in this Project.",
                    code="source_relationship_target_not_found",
                )
            if target.id == revision.id:
                raise BadRequestError(
                    message="A source revision cannot relate to itself.",
                    code="source_relationship_self_reference",
                )
            if (
                relation.relationship_type == SourceRelationshipType.REPLACES
                and target.source_group_id != revision.source_group_id
            ):
                raise BadRequestError(
                    message="A replaces relationship must stay within one source group.",
                    code="source_replaces_group_mismatch",
                )
            if (
                relation.relationship_type == SourceRelationshipType.MODIFIES
                and target.source_group_id == revision.source_group_id
            ):
                raise BadRequestError(
                    message="A modifying source must use a separate source group.",
                    code="source_modifies_group_conflict",
                )
            self._repository.add(
                SourceRevisionRelationship(
                    id=uuid.uuid4(),
                    project_id=self._repository.project_id,
                    source_revision_id=revision.id,
                    target_revision_id=target.id,
                    relationship_type=relation.relationship_type,
                    target_provisions=relation.target_provisions,
                )
            )

        self._record(
            AuditEventType.SOURCE_METADATA_REVISION_CREATED,
            revision,
            detail={
                "document_id": str(document.id),
                "source_group_id": str(group.id),
                "revision_number": next_number,
                "relationship_count": len(data.relationships),
            },
        )
        return revision

    async def _stage_activation(
        self,
        project: Project,
        revision: SourceMetadataRevision,
        *,
        reason: str,
    ) -> SourceActivationEvent:
        if revision.project_id != project.id:
            raise BadRequestError(
                message="Source revision and Project scope do not match.",
                code="source_project_scope_mismatch",
            )
        project.source_metadata_generation += 1
        activation = SourceActivationEvent(
            id=uuid.uuid4(),
            project_id=project.id,
            document_id=revision.document_id,
            source_revision_id=revision.id,
            generation=project.source_metadata_generation,
            activated_by=self._actor_id,
            reason=reason,
        )
        self._repository.add(activation)
        self._record(
            AuditEventType.SOURCE_METADATA_REVISION_ACTIVATED,
            revision,
            detail={
                "document_id": str(revision.document_id),
                "generation": activation.generation,
                "reason": reason,
            },
        )
        await self._repository.flush()
        return activation

    async def _response_context(
        self,
        revisions: list[SourceMetadataRevision],
    ) -> tuple[dict[uuid.UUID, list[SourceRevisionRelationship]], set[uuid.UUID]]:
        revision_ids = [revision.id for revision in revisions]
        return (
            await self._repository.relationships_for(revision_ids),
            await self._repository.overlapping_revision_ids(revision_ids),
        )

    async def _revision_response(
        self,
        revision: SourceMetadataRevision,
        *,
        relationships: dict[uuid.UUID, list[SourceRevisionRelationship]] | None = None,
        overlaps: set[uuid.UUID] | None = None,
    ) -> SourceRevisionResponse:
        if relationships is None:
            relationships = await self._repository.relationships_for([revision.id])
        revision_relationships = relationships.get(revision.id, [])
        warnings: list[str] = []
        if revision.lifecycle_status == SourceLifecycleStatus.ACTIVE and (
            revision.effective_from is None or revision.effective_to is None
        ):
            warnings.append("missing_effective_dates")
        has_overlap = (
            revision.id in overlaps
            if overlaps is not None
            else await self._repository.has_overlapping_revision(
                revision.source_group_id,
                effective_from=revision.effective_from,
                effective_to=revision.effective_to,
                exclude_revision_id=revision.id,
            )
        )
        if has_overlap:
            warnings.append("overlapping_effective_interval")
        return SourceRevisionResponse(
            id=revision.id,
            project_id=revision.project_id,
            document_id=revision.document_id,
            source_group_id=revision.source_group_id,
            revision_number=revision.revision_number,
            revision_label=revision.revision_label,
            title=revision.title,
            source_type=revision.source_type,
            published_date=revision.published_date,
            effective_from=revision.effective_from,
            effective_to=revision.effective_to,
            lifecycle_status=revision.lifecycle_status,
            source_role=revision.source_role,
            change_reason=revision.change_reason,
            created_by=revision.created_by,
            content_hash=revision.content_hash,
            created_at=revision.created_at,
            relationships=[
                SourceRelationshipResponse.model_validate(relation)
                for relation in revision_relationships
            ],
            warnings=warnings,
        )

    async def _require_locked_project(self) -> Project:
        project = await self._repository.lock_project()
        if project is None:
            raise NotFoundError(
                message=_PROJECT_NOT_FOUND["message"], code=_PROJECT_NOT_FOUND["code"]
            )
        return project

    async def _require_project(self) -> Project:
        project = await self._session.get(Project, self._repository.project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError(
                message=_PROJECT_NOT_FOUND["message"], code=_PROJECT_NOT_FOUND["code"]
            )
        return project

    async def _require_document(self, document_id: uuid.UUID) -> Document:
        document = await self._repository.get_document(document_id)
        if document is None:
            raise NotFoundError(
                message=_DOCUMENT_NOT_FOUND["message"], code=_DOCUMENT_NOT_FOUND["code"]
            )
        return document

    async def _require_revision(self, revision_id: uuid.UUID) -> SourceMetadataRevision:
        revision = await self._repository.get_revision(revision_id)
        if revision is None:
            raise NotFoundError(
                message=_REVISION_NOT_FOUND["message"], code=_REVISION_NOT_FOUND["code"]
            )
        return revision

    def _record(
        self,
        event_type: AuditEventType,
        revision: SourceMetadataRevision,
        *,
        detail: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        self._audit.record(
            event_type=event_type,
            actor_type=AuditActorType.OPERATOR,
            actor_id=self._actor_id,
            project_id=revision.project_id,
            resource_type="source_metadata_revision",
            resource_id=revision.id,
            outcome=AuditOutcome.SUCCESS,
            detail=detail,
        )
