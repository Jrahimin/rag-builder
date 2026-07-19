"""Unit tests for the pgvector startup/readiness contract."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.platform.db.session import Database, MigrationStateError, PgVectorUnavailableError

pytestmark = pytest.mark.unit


class _ConnectionContext:
    def __init__(self, connection: AsyncMock) -> None:
        self._connection = connection

    async def __aenter__(self) -> AsyncMock:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _Engine:
    def __init__(self, connection: AsyncMock) -> None:
        self._connection = connection

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self._connection)


def _database(*scalar_values: object) -> Database:
    connection = AsyncMock()
    connection.scalar = AsyncMock(side_effect=scalar_values)
    database = object.__new__(Database)
    database._embedding_dimensions = 384
    database._engine = _Engine(connection)
    return database


async def test_check_rejects_missing_pgvector_extension() -> None:
    with pytest.raises(PgVectorUnavailableError, match="CREATE EXTENSION vector"):
        await _database(None).check_pgvector()


async def test_check_rejects_wrong_vector_dimension() -> None:
    with pytest.raises(PgVectorUnavailableError, match=r"expected vector\(384\)"):
        await _database("0.8.1", "vector(1536)").check_pgvector()


async def test_check_accepts_matching_extension_and_dimension() -> None:
    await _database("0.8.1", "vector(384)").check_pgvector()


class _ScalarRows:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def scalars(self) -> _ScalarRows:
        return self

    def all(self) -> list[str]:
        return self._values


async def test_migration_check_rejects_database_behind_head() -> None:
    database = _database()
    connection = database._engine._connection
    connection.execute = AsyncMock(return_value=_ScalarRows(["old-revision"]))

    with pytest.raises(MigrationStateError, match="alembic upgrade head"):
        await database.check_migrations()
