"""Project-scoped repository base — mandatory for Project-owned entities."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.platform.db.base import Base
from app.platform.persistence.filters import apply_deterministic_order


class ProjectScopedRepository[ModelT: Base]:
    """Async CRUD scoped by ``project_id`` with deterministic list ordering.

    Every query includes ``project_id`` so cross-Project access is impossible
    through the repository API. Models must compose :class:`ProjectScopedMixin`.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> uuid.UUID:
        return self._project_id

    def _scoped(self) -> Select[tuple[ModelT]]:
        """Base select filtered to this repository's Project."""
        project_col = getattr(self.model, "project_id", None)
        if project_col is None:
            msg = f"{self.model.__name__} must compose ProjectScopedMixin"
            raise TypeError(msg)
        return select(self.model).where(project_col == self._project_id)

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Fetch by primary key within this Project, or ``None``."""
        entity_id_col = getattr(self.model, "id", None)
        if entity_id_col is None:
            msg = f"{self.model.__name__} must expose id (use UUIDPrimaryKeyMixin)"
            raise TypeError(msg)
        result = await self._session.execute(self._scoped().where(entity_id_col == entity_id))
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        """Return a page ordered by ``created_at``, then ``id`` (deterministic)."""
        stmt = self._scoped().limit(limit).offset(offset)
        stmt = apply_deterministic_order(stmt, self.model)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self) -> int:
        """Count rows for this Project only."""
        project_col: InstrumentedAttribute[Any] = self.model.project_id  # type: ignore[attr-defined]
        result = await self._session.execute(
            select(func.count()).select_from(self.model).where(project_col == self._project_id)
        )
        return int(result.scalar_one())

    def add(self, entity: ModelT) -> ModelT:
        """Stage an insert; entity ``project_id`` must match this repository."""
        entity_project_id = getattr(entity, "project_id", None)
        if entity_project_id is None:
            msg = f"{type(entity).__name__} must expose project_id (use ProjectScopedMixin)"
            raise TypeError(msg)
        if entity_project_id != self._project_id:
            msg = "Entity project_id does not match repository scope"
            raise ValueError(msg)
        self._session.add(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        entity_project_id = getattr(entity, "project_id", None)
        if entity_project_id is not None and entity_project_id != self._project_id:
            msg = "Cannot delete entity outside repository project scope"
            raise ValueError(msg)
        await self._session.delete(entity)

    async def flush(self) -> None:
        await self._session.flush()
