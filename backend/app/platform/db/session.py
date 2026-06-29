"""Async SQLAlchemy engine and session management.

Wraps the async engine plus session factory in a small :class:`Database`
object whose lifecycle is owned by the application lifespan. The engine is
created at startup and disposed at shutdown; request handlers obtain sessions
through dependency injection (see ``app.dependencies.database``).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class Database:
    """Owns the async engine and session factory for a deployment."""

    def __init__(self, settings: Settings) -> None:
        db = settings.database
        self._engine: AsyncEngine = create_async_engine(
            db.async_dsn,
            echo=db.echo,
            pool_size=db.pool_size,
            max_overflow=db.max_overflow,
            pool_timeout=db.pool_timeout,
            pool_recycle=db.pool_recycle,
            pool_pre_ping=True,
            future=True,
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def check(self) -> None:
        """Run a trivial query to verify connectivity (raises on failure)."""
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Dispose of the connection pool on shutdown."""
        await self._engine.dispose()
        log.info("database_disposed")
