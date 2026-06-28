"""Reusable FastAPI dependencies and typed dependency aliases.

Centralizes how routers obtain configuration, database sessions, infrastructure
clients, and services. Infrastructure objects are created once during the
application lifespan and stored on ``app.state``; these dependencies simply
expose them to handlers, keeping wiring explicit and testable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.qdrant import QdrantConnection
from app.db.redis import RedisClient
from app.db.session import Database
from app.services.health_service import HealthService


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_redis_client(request: Request) -> RedisClient:
    return request.app.state.redis


def get_qdrant_connection(request: Request) -> QdrantConnection:
    return request.app.state.qdrant


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session; the service layer owns commits."""
    database = get_database(request)
    async with database.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_redis(request: Request) -> Redis:
    return get_redis_client(request).client


def get_qdrant(request: Request) -> AsyncQdrantClient:
    return get_qdrant_connection(request).client


def get_health_service(request: Request) -> HealthService:
    return HealthService(
        settings=get_settings(),
        database=get_database(request),
        redis_client=get_redis_client(request),
        qdrant=get_qdrant_connection(request),
    )


# --- Typed dependency aliases (use these in route signatures) ---------------
SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
QdrantDep = Annotated[AsyncQdrantClient, Depends(get_qdrant)]
HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
