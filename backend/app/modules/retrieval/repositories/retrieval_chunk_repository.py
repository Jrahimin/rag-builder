"""Document chunk access for retrieval — project-scoped reads."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document_chunk import DocumentChunk
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class RetrievalChunkRepository(ProjectScopedRepository[DocumentChunk]):
    """Read document chunks within a Project for retrieval workflows."""

    model = DocumentChunk

    async def adjacent_ids(
        self, anchor_ids: list[uuid.UUID], *, index_build_id: uuid.UUID
    ) -> tuple[uuid.UUID, ...]:
        """Neighbouring pages (or chunks for unpaged text), in one indexed version.

        Table evidence units need not have consecutive chunk indices in page order.
        Exclude the anchors themselves so they cannot crowd out the missing context.
        """
        if not anchor_ids:
            return ()
        anchor = aliased(DocumentChunk)
        anchor_index = aliased(ChunkKeywordIndex)
        page = func.coalesce(self.model.page_start, self.model.page_number)
        anchor_page = func.coalesce(anchor.page_start, anchor.page_number)
        adjacent = or_(
            and_(
                page.is_not(None),
                anchor_page.is_not(None),
                page.between(anchor_page - 1, anchor_page + 1),
            ),
            and_(
                or_(page.is_(None), anchor_page.is_(None)),
                self.model.chunk_index.between(anchor.chunk_index - 1, anchor.chunk_index + 1),
            ),
        )
        stmt = (
            select(self.model.id)
            .distinct()
            .join(
                anchor,
                (anchor.document_id == self.model.document_id)
                & (anchor.document_version == self.model.document_version)
                & (anchor.project_id == self.model.project_id)
                & adjacent,
            )
            .join(
                anchor_index,
                (anchor_index.chunk_id == anchor.id)
                & (anchor_index.project_id == anchor.project_id),
            )
            .join(
                ChunkKeywordIndex,
                (ChunkKeywordIndex.chunk_id == self.model.id)
                & (ChunkKeywordIndex.project_id == self.model.project_id),
            )
            .where(
                self.model.project_id == self._project_id,
                anchor.id.in_(anchor_ids[:4]),
                self.model.id.not_in(anchor_ids[:4]),
                anchor_index.index_build_id == index_build_id,
                ChunkKeywordIndex.index_build_id == index_build_id,
            )
            .order_by(self.model.id)
            .limit(48)
        )
        result = await self._session.execute(stmt)
        return tuple(result.scalars().all())

    async def list_by_document(
        self, document_id: uuid.UUID, *, document_version: int | None = None
    ) -> list[DocumentChunk]:
        """Return all chunks for a document ordered by ``chunk_index``."""
        stmt = (
            self._scoped()
            .where(self.model.document_id == document_id)
            .order_by(self.model.chunk_index)
        )
        if document_version is not None:
            stmt = stmt.where(self.model.document_version == document_version)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def map_by_ids(
        self,
        chunk_ids: list[uuid.UUID],
        *,
        document_id: uuid.UUID | None = None,
    ) -> dict[uuid.UUID, DocumentChunk]:
        """Return chunks keyed by id, optionally scoped to one document."""
        if not chunk_ids:
            return {}
        stmt = self._scoped().where(self.model.id.in_(chunk_ids))
        if document_id is not None:
            stmt = stmt.where(self.model.document_id == document_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.id: row for row in rows}
