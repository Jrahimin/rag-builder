"""Project persistence — extends the shared async repository."""

from __future__ import annotations

import uuid

from app.models.project import Project
from app.platform.persistence.async_repository import AsyncRepository


class ProjectRepository(AsyncRepository[Project]):
    """Async CRUD for the Project aggregate root (unscoped by design)."""

    model = Project

    async def exists_by_name(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        return await self.exists_by_field("name", name, exclude_id=exclude_id, not_deleted=True)
