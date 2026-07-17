"""Document persistence — project-scoped with lifecycle list filters."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.document import Document
from app.platform.persistence.filters import column_equals, not_deleted_filter
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class DocumentRepository(ProjectScopedRepository[Document]):
    """Async CRUD for documents within a single Project."""

    model = Document

    async def exists_by_content_sha256(self, content_sha256: str) -> bool:
        clauses = [
            column_equals(self.model, "content_sha256", content_sha256),
            not_deleted_filter(self.model),
        ]
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.project_id == self._project_id)
            .where(*clauses)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one()) > 0
