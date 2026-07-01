"""Document chunk persistence — project- and document-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select

from app.models.document_chunk import DocumentChunk
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class DocumentChunkRepository(ProjectScopedRepository[DocumentChunk]):
    """Async access to chunks within a Project and Document."""

    model = DocumentChunk

    async def list_by_document(
        self,
        document_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[DocumentChunk]:
        stmt = (
            self._scoped()
            .where(self.model.document_id == document_id)
            .order_by(self.model.chunk_index)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_document(self, document_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.project_id == self._project_id)
            .where(self.model.document_id == document_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def delete_by_document(self, document_id: uuid.UUID) -> None:
        stmt = (
            delete(self.model)
            .where(self.model.project_id == self._project_id)
            .where(self.model.document_id == document_id)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    def bulk_add(self, chunks: list[DocumentChunk]) -> None:
        for chunk in chunks:
            self.add(chunk)

    async def flush(self) -> None:
        await self._session.flush()
