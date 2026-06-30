"""Shared pytest fixtures.

Forces the ``testing`` environment *before* importing the application so a
developer's local ``.env`` never bleeds into the test run.

Integration tests that migrate PostgreSQL require an explicit disposable
database (``APE_DATABASE__NAME`` must match ``APE_TEST_DATABASE__NAME`` and
``APE_TEST_DATABASE__ALLOW_MIGRATIONS=true``).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

os.environ["APE_APP__ENV"] = "testing"
os.environ.setdefault("APE_DATABASE__NAME", "ape_test")
os.environ.setdefault("APE_TEST_DATABASE__NAME", "ape_test")
os.environ.setdefault("APE_TEST_DATABASE__ALLOW_MIGRATIONS", "true")
os.environ.setdefault("APE_LOGGING__RENDER_JSON", "false")

from app.core.config import Settings, get_settings
from app.dependencies.common import get_db_session
from app.main import create_app
from app.platform.db.session import Database


def _integration_db_allowed(settings: Settings) -> tuple[bool, str]:
    """Return whether integration DB fixtures may run and a skip reason."""
    if settings.database.name != settings.test_database.name:
        return (
            False,
            "Refusing to run integration DB tests against "
            f"{settings.database.name!r}. Set APE_DATABASE__NAME to the disposable "
            f"test database ({settings.test_database.name!r}).",
        )
    if not settings.test_database.allow_migrations:
        return (
            False,
            "Set APE_TEST_DATABASE__ALLOW_MIGRATIONS=true to run integration DB migrations.",
        )
    return True, ""


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the ASGI app with lifespan executed.

    The lifespan is resilient to unavailable dependencies, so this works even
    when no external services (Postgres/Redis/Qdrant/MinIO) are running.
    """
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac


# --- PostgreSQL integration fixtures --------------------------------------------


@pytest.fixture(scope="session")
def postgres_available(settings: Settings) -> bool:
    """Return whether PostgreSQL is reachable for integration tests."""
    allowed, _ = _integration_db_allowed(settings)
    if not allowed:
        return False

    async def _check() -> bool:
        engine = create_async_engine(settings.database.async_dsn, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    import asyncio

    return asyncio.run(_check())


@pytest.fixture(scope="session")
def require_postgres(settings: Settings, postgres_available: bool) -> None:
    allowed, reason = _integration_db_allowed(settings)
    if not allowed:
        pytest.skip(reason)
    if not postgres_available:
        pytest.skip("PostgreSQL not available")


@pytest.fixture(scope="session")
def apply_migrations(require_postgres: None, settings: Settings) -> None:
    """Apply Alembic migrations once per test session."""
    from alembic import command
    from alembic.config import Config

    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db_client(
    require_postgres: None,
    apply_migrations: None,
    settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """HTTP client with DB session override (rolled back after each test)."""
    get_settings.cache_clear()
    app = create_app()
    database = Database(settings)
    connection: AsyncConnection = await database.engine.connect()
    transaction = await connection.begin()

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac

    app.dependency_overrides.clear()
    await transaction.rollback()
    await connection.close()
    await database.dispose()
