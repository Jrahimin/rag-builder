"""Project-scoped persistence for source metadata and activation history."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.document import Document
from app.models.project import Project
from app.models.source_metadata import (
    SourceActivationEvent,
    SourceGroup,
    SourceMetadataRevision,
    SourceRevisionRelationship,
)


class SourceMetadataRepository:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._session = session
        self.project_id = project_id

    async def lock_project(self) -> Project | None:
        result = await self._session.execute(
            select(Project)
            .where(Project.id == self.project_id, Project.deleted_at.is_(None))
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_document(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.project_id == self.project_id,
                Document.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_group(self, group_id: uuid.UUID) -> SourceGroup | None:
        result = await self._session.execute(
            select(SourceGroup).where(
                SourceGroup.id == group_id,
                SourceGroup.project_id == self.project_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_revision(self, revision_id: uuid.UUID) -> SourceMetadataRevision | None:
        result = await self._session.execute(
            select(SourceMetadataRevision).where(
                SourceMetadataRevision.id == revision_id,
                SourceMetadataRevision.project_id == self.project_id,
            )
        )
        return result.scalar_one_or_none()

    async def next_revision_number(self, group_id: uuid.UUID) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(SourceMetadataRevision.revision_number), 0)).where(
                SourceMetadataRevision.project_id == self.project_id,
                SourceMetadataRevision.source_group_id == group_id,
            )
        )
        return int(value or 0) + 1

    async def latest_for_document(
        self,
        document_id: uuid.UUID,
        *,
        generation: int | None = None,
    ) -> tuple[SourceActivationEvent, SourceMetadataRevision] | None:
        project = await self._session.get(Project, self.project_id)
        resolved_generation = (
            generation
            if generation is not None
            else (project.source_metadata_generation if project else 0)
        )
        result = await self._session.execute(
            select(SourceActivationEvent, SourceMetadataRevision)
            .join(
                SourceMetadataRevision,
                SourceMetadataRevision.id == SourceActivationEvent.source_revision_id,
            )
            .where(
                SourceActivationEvent.project_id == self.project_id,
                SourceActivationEvent.document_id == document_id,
                SourceActivationEvent.generation <= resolved_generation,
            )
            .order_by(
                SourceActivationEvent.generation.desc(),
                SourceActivationEvent.created_at.desc(),
            )
            .limit(1)
        )
        row = result.first()
        return (row[0], row[1]) if row is not None else None

    async def state_at(
        self, generation: int
    ) -> list[tuple[SourceActivationEvent, SourceMetadataRevision]]:
        ranked = (
            select(
                SourceActivationEvent.id.label("activation_id"),
                func.row_number()
                .over(
                    partition_by=SourceActivationEvent.document_id,
                    order_by=(
                        SourceActivationEvent.generation.desc(),
                        SourceActivationEvent.created_at.desc(),
                    ),
                )
                .label("position"),
            )
            .where(
                SourceActivationEvent.project_id == self.project_id,
                SourceActivationEvent.generation <= generation,
            )
            .subquery()
        )
        result = await self._session.execute(
            select(SourceActivationEvent, SourceMetadataRevision)
            .join(ranked, ranked.c.activation_id == SourceActivationEvent.id)
            .join(
                SourceMetadataRevision,
                SourceMetadataRevision.id == SourceActivationEvent.source_revision_id,
            )
            .where(ranked.c.position == 1)
            .order_by(SourceMetadataRevision.title, SourceMetadataRevision.document_id)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_document_revisions(
        self, document_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[SourceMetadataRevision]:
        result = await self._session.execute(
            select(SourceMetadataRevision)
            .where(
                SourceMetadataRevision.project_id == self.project_id,
                SourceMetadataRevision.document_id == document_id,
            )
            .order_by(SourceMetadataRevision.created_at.desc(), SourceMetadataRevision.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_activations(
        self, *, document_id: uuid.UUID | None, limit: int, offset: int
    ) -> list[SourceActivationEvent]:
        clauses = [SourceActivationEvent.project_id == self.project_id]
        if document_id is not None:
            clauses.append(SourceActivationEvent.document_id == document_id)
        result = await self._session.execute(
            select(SourceActivationEvent)
            .where(*clauses)
            .order_by(SourceActivationEvent.generation.desc(), SourceActivationEvent.created_at)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def relationships_for(
        self, revision_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[SourceRevisionRelationship]]:
        if not revision_ids:
            return {}
        result = await self._session.execute(
            select(SourceRevisionRelationship)
            .where(
                SourceRevisionRelationship.project_id == self.project_id,
                SourceRevisionRelationship.source_revision_id.in_(revision_ids),
            )
            .order_by(SourceRevisionRelationship.created_at, SourceRevisionRelationship.id)
        )
        grouped: dict[uuid.UUID, list[SourceRevisionRelationship]] = {}
        for relation in result.scalars().all():
            grouped.setdefault(relation.source_revision_id, []).append(relation)
        return grouped

    async def has_overlapping_revision(
        self,
        group_id: uuid.UUID,
        *,
        effective_from: date | None,
        effective_to: date | None,
        exclude_revision_id: uuid.UUID,
    ) -> bool:
        if effective_from is None or effective_to is None:
            return False
        count = await self._session.scalar(
            select(func.count())
            .select_from(SourceMetadataRevision)
            .where(
                SourceMetadataRevision.project_id == self.project_id,
                SourceMetadataRevision.source_group_id == group_id,
                SourceMetadataRevision.id != exclude_revision_id,
                SourceMetadataRevision.effective_from.is_not(None),
                SourceMetadataRevision.effective_to.is_not(None),
                and_(
                    SourceMetadataRevision.effective_from <= effective_to,
                    SourceMetadataRevision.effective_to >= effective_from,
                ),
            )
        )
        return int(count or 0) > 0

    async def overlapping_revision_ids(
        self,
        revision_ids: list[uuid.UUID],
    ) -> set[uuid.UUID]:
        """Batch overlap warnings for list/state response construction."""
        if not revision_ids:
            return set()
        target = aliased(SourceMetadataRevision)
        other = aliased(SourceMetadataRevision)
        result = await self._session.execute(
            select(target.id)
            .join(
                other,
                and_(
                    other.project_id == target.project_id,
                    other.source_group_id == target.source_group_id,
                    other.id != target.id,
                    other.effective_from.is_not(None),
                    other.effective_to.is_not(None),
                    other.effective_from <= target.effective_to,
                    other.effective_to >= target.effective_from,
                ),
            )
            .where(
                target.project_id == self.project_id,
                target.id.in_(revision_ids),
                target.effective_from.is_not(None),
                target.effective_to.is_not(None),
            )
            .distinct()
        )
        return set(result.scalars().all())

    def add(self, entity: object) -> None:
        self._session.add(entity)

    async def flush(self) -> None:
        await self._session.flush()

    async def purge_document_artifacts(self, document_id: uuid.UUID) -> None:
        """Remove source metadata rows that block irreversible document purge."""
        revision_ids = list(
            await self._session.scalars(
                select(SourceMetadataRevision.id).where(
                    SourceMetadataRevision.project_id == self.project_id,
                    SourceMetadataRevision.document_id == document_id,
                )
            )
        )
        if not revision_ids:
            return

        await self._session.execute(
            delete(SourceRevisionRelationship).where(
                SourceRevisionRelationship.project_id == self.project_id,
                (
                    SourceRevisionRelationship.source_revision_id.in_(revision_ids)
                    | SourceRevisionRelationship.target_revision_id.in_(revision_ids)
                ),
            )
        )
        await self._session.execute(
            delete(SourceActivationEvent).where(
                SourceActivationEvent.project_id == self.project_id,
                SourceActivationEvent.document_id == document_id,
            )
        )
        group_ids = list(
            await self._session.scalars(
                select(SourceMetadataRevision.source_group_id).where(
                    SourceMetadataRevision.project_id == self.project_id,
                    SourceMetadataRevision.document_id == document_id,
                )
            )
        )
        await self._session.execute(
            delete(SourceMetadataRevision).where(
                SourceMetadataRevision.project_id == self.project_id,
                SourceMetadataRevision.document_id == document_id,
            )
        )
        for group_id in set(group_ids):
            remaining = await self._session.scalar(
                select(func.count())
                .select_from(SourceMetadataRevision)
                .where(SourceMetadataRevision.source_group_id == group_id)
            )
            if not int(remaining or 0):
                await self._session.execute(
                    delete(SourceGroup).where(
                        SourceGroup.id == group_id,
                        SourceGroup.project_id == self.project_id,
                    )
                )
