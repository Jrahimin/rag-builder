"""Generic asynchronous repository base class.

Repositories are the *only* layer that talks to the relational database. They
encapsulate persistence (CRUD, entity mapping) and never contain business
logic, call LLMs, or touch the vector store.

Concrete, Project-scoped repositories will subclass :class:`BaseRepository`
and add ``project_id`` filtering to every query (see architecture: Repository
Layer + Project Isolation). This base provides the common, unscoped plumbing.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


class BaseRepository[ModelT: Base]:
    """Common async CRUD operations for a single ORM model."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: object) -> ModelT | None:
        """Fetch a single entity by primary key, or ``None``."""
        return await self._session.get(self.model, entity_id)

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        """Return a page of entities ordered by the database default."""
        result = await self._session.execute(select(self.model).limit(limit).offset(offset))
        return list(result.scalars().all())

    async def count(self) -> int:
        """Return the total number of rows for this model."""
        result = await self._session.execute(select(func.count()).select_from(self.model))
        return int(result.scalar_one())

    def add(self, entity: ModelT) -> ModelT:
        """Stage a new entity for insertion (flush/commit handled by service)."""
        self._session.add(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        """Stage an entity for deletion."""
        await self._session.delete(entity)

    async def flush(self) -> None:
        """Flush pending changes to obtain generated values (e.g. ids)."""
        await self._session.flush()
