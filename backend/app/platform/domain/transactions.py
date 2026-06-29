"""Transaction helpers for service-layer commit boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError


class _Flushable(Protocol):
    async def flush(self) -> None: ...


async def commit_refresh[T](session: AsyncSession, entity: T) -> T:
    """Commit the session and refresh the entity."""
    await session.commit()
    await session.refresh(entity)
    return entity


async def flush_commit_refresh[T](
    session: AsyncSession,
    repository: _Flushable,
    entity: T,
    *,
    on_integrity: Callable[[], ConflictError] | None = None,
) -> T:
    """Flush and commit, then refresh. Rolls back on flush/commit ``IntegrityError``."""
    try:
        await repository.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if on_integrity is not None:
            raise on_integrity() from exc
        raise
    await session.refresh(entity)
    return entity
