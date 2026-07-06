"""Document chunk access for retrieval — project-scoped reads."""

from __future__ import annotations

import uuid

from app.models.document_chunk import DocumentChunk
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class RetrievalChunkRepository(ProjectScopedRepository[DocumentChunk]):
    """Read document chunks within a Project for retrieval workflows."""

    model = DocumentChunk

    async def list_by_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        """Return all chunks for a document ordered by ``chunk_index``."""
        stmt = (
            self._scoped()
            .where(self.model.document_id == document_id)
            .order_by(self.model.chunk_index)
        )
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
