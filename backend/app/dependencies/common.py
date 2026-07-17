"""Reusable FastAPI dependencies — composition root for HTTP wiring.

Feature modules must **not** import from this package. Only ``api/`` routers
and the application entrypoint use these dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.platform.db.session import Database
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.system.health_service import HealthService


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_redis_connectivity(request: Request) -> RedisConnectivity:
    return request.app.state.redis


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session; the service layer owns commits."""
    database = get_database(request)
    async with database.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_health_service(request: Request) -> HealthService:
    return HealthService(
        settings=request.app.state.settings,
        database=get_database(request),
        redis=get_redis_connectivity(request),
        storage=request.app.state.storage,
        preflight=request.app.state.preflight,
    )


# --- Typed dependency aliases (use in api/ and composition layer only) ------
SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
