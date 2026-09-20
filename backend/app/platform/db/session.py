"""Async SQLAlchemy engine and session management.

Wraps the async engine plus session factory in a small :class:`Database`
object whose lifecycle is owned by the application lifespan. The engine is
created at startup and disposed at shutdown; request handlers obtain sessions
through dependency injection (see ``app.dependencies.database``).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import Pool

from app.core.config import Settings
from app.core.logging import get_logger
from app.platform.providers.request_work import RequestWork, current_request_work

log = get_logger(__name__)
_MIGRATION_ROOT = Path(__file__).resolve().parents[2] / "composition" / "migrations"
_ResultT = TypeVar("_ResultT")


@dataclass
class _AcquisitionProbe:
    work: RequestWork
    started: float
    recorded: bool = False


_acquisition_probe: ContextVar[_AcquisitionProbe | None] = ContextVar(
    "database_acquisition_probe", default=None
)


@event.listens_for(Pool, "checkout")
def _record_pool_checkout(*_args: object) -> None:
    """Complete a pending request probe only when the pool actually checks out."""
    probe = _acquisition_probe.get()
    if probe is None or probe.recorded:
        return
    probe.recorded = True
    probe.work.record_wait("database_connection_acquisition", probe.started)


class PgVectorUnavailableError(RuntimeError):
    """Raised when PostgreSQL is reachable but the vector extension is absent."""


class MigrationStateError(RuntimeError):
    """Raised when the database revision does not match the checked-in migration head."""


class ObservedAsyncSession(AsyncSession):
    """Record connection acquisition, including pool wait, creation, and pre-ping."""

    async def _with_acquisition_probe(
        self, operation: Callable[[], Awaitable[_ResultT]]
    ) -> _ResultT:
        work = current_request_work()
        if work is None:
            return await operation()
        probe = _AcquisitionProbe(work=work, started=time.perf_counter())
        token: Token[_AcquisitionProbe | None] = _acquisition_probe.set(probe)
        try:
            return await operation()
        except SQLAlchemyTimeoutError as exc:
            if not probe.recorded:
                work.record_wait(
                    "database_connection_acquisition", probe.started, outcome="failed", error=exc
                )
            raise
        finally:
            _acquisition_probe.reset(token)

    async def connection(
        self,
        bind_arguments: dict[str, Any] | None = None,
        execution_options: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncConnection:
        async def connect() -> AsyncConnection:
            return await super(ObservedAsyncSession, self).connection(
                bind_arguments=bind_arguments,
                execution_options=execution_options,
                **kwargs,
            )

        return await self._with_acquisition_probe(connect)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).execute(*args, **kwargs)
        )

    async def scalar(self, *args: Any, **kwargs: Any) -> Any:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).scalar(*args, **kwargs)
        )

    async def scalars(self, *args: Any, **kwargs: Any) -> Any:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).scalars(*args, **kwargs)
        )

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).stream(*args, **kwargs)
        )

    async def stream_scalars(self, *args: Any, **kwargs: Any) -> Any:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).stream_scalars(*args, **kwargs)
        )

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).get(*args, **kwargs)
        )

    async def flush(self, objects: Any = None) -> None:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).flush(objects)
        )

    async def commit(self) -> None:
        return await self._with_acquisition_probe(
            lambda: super(ObservedAsyncSession, self).commit()
        )


class Database:
    """Owns the async engine and session factory for a deployment."""

    def __init__(self, settings: Settings) -> None:
        db = settings.database
        self._embedding_dimensions = settings.embedding.dimensions
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
            class_=ObservedAsyncSession,
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
        """Verify connectivity, migration compatibility, and pgvector dimensions."""
        await self.check_connection()
        await self.check_migrations()
        await self.check_pgvector()

    async def check_connection(self) -> None:
        """Verify that PostgreSQL accepts a simple query."""
        async with self._engine.connect() as conn:
            await conn.scalar(text("SELECT 1"))

    async def check_migrations(self) -> None:
        """Require the database Alembic revision to equal the repository head."""
        expected_heads = set(ScriptDirectory(str(_MIGRATION_ROOT)).get_heads())
        async with self._engine.connect() as conn:
            rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current_heads = {str(value) for value in rows.scalars().all()}
        if current_heads != expected_heads:
            message = (
                "Database migrations are not at the repository head "
                f"(expected {sorted(expected_heads)}, found {sorted(current_heads)}). "
                "Run `alembic upgrade head` before serving traffic."
            )
            raise MigrationStateError(message)

    async def check_pgvector(self) -> None:
        """Verify pgvector is enabled and its column matches configured dimensions."""
        async with self._engine.connect() as conn:
            extension_version = await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            if extension_version is None:
                message = (
                    "PostgreSQL is reachable, but the required pgvector extension is "
                    "not enabled. Run `CREATE EXTENSION vector` as a privileged role "
                    "and then apply `alembic upgrade head`."
                )
                raise PgVectorUnavailableError(message)
            column_type = await conn.scalar(
                text(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                    FROM pg_attribute AS a
                    JOIN pg_class AS c ON c.oid = a.attrelid
                    WHERE c.relname = 'chunk_embeddings'
                      AND a.attname = 'embedding'
                      AND NOT a.attisdropped
                    """
                )
            )
            expected_type = f"vector({self._embedding_dimensions})"
            if column_type != expected_type:
                message = (
                    "The pgvector embedding column is missing or has the wrong "
                    f"dimension (expected {expected_type}, found {column_type!r}). "
                    "Apply `alembic upgrade head`; model dimension changes require "
                    "a schema migration and re-embedding."
                )
                raise PgVectorUnavailableError(message)

    async def dispose(self) -> None:
        """Dispose of the connection pool on shutdown."""
        await self._engine.dispose()
        log.info("database_disposed")
