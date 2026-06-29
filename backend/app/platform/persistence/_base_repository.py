"""Internal repository helpers — not exported to feature modules."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db.base import Base


class _BaseRepository[ModelT: Base]:
    """Low-level async CRUD without project scoping.

    Feature modules must use :class:`ProjectScopedRepository` instead.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: object) -> ModelT | None:
        return await self._session.get(self.model, entity_id)

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        result = await self._session.execute(select(self.model).limit(limit).offset(offset))
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(self.model))
        return int(result.scalar_one())

    def add(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self._session.delete(entity)

    async def flush(self) -> None:
        await self._session.flush()
