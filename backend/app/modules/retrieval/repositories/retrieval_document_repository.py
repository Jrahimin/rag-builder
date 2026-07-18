"""Thin document access for retrieval — status updates only."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement

from app.models.document import Document
from app.platform.persistence.filters import not_deleted_filter
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class RetrievalDocumentRepository(ProjectScopedRepository[Document]):
    """Project-scoped document reads and status updates for retrieval."""

    model = Document

    async def get_by_id(
        self,
        document_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        for_update: bool = False,
    ) -> Document | None:
        clauses: list[ColumnElement[bool]] = [self.model.id == document_id]
        if not include_deleted:
            clauses.append(not_deleted_filter(self.model))
        stmt = self._scoped().where(*clauses)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def map_by_ids(self, document_ids: set[uuid.UUID]) -> dict[uuid.UUID, Document]:
        """Return documents keyed by id; the active build is the retrieval authority."""
        if not document_ids:
            return {}
        stmt = self._scoped().where(self.model.id.in_(document_ids))
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.id: row for row in rows}
