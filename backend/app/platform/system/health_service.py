"""Deployment-level health and readiness orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from time import perf_counter

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.platform.db.session import Database
from app.platform.infra.connectivity.qdrant import QdrantConnectivity
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.system.schemas import (
    DependencyHealth,
    DependencyState,
    LivenessStatus,
    ReadinessStatus,
)

log = get_logger(__name__)

_CHECK_TIMEOUT_SECONDS = 3.0


class HealthService:
    """Aggregates liveness and readiness across platform dependencies."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        redis: RedisConnectivity,
        qdrant: QdrantConnectivity,
    ) -> None:
        self._settings = settings
        self._database = database
        self._redis = redis
        self._qdrant = qdrant

    def liveness(self) -> LivenessStatus:
        return LivenessStatus(
            service=self._settings.app.name,
            version=self._settings.app.version,
            environment=self._settings.app.env.value,
        )

    async def readiness(self) -> ReadinessStatus:
        dependencies = await asyncio.gather(
            self._timed_check("postgresql", self._database.check()),
            self._timed_check("redis", self._redis.check()),
            self._timed_check("qdrant", self._qdrant.check()),
            self._check_minio(),
        )
        healthy = all(
            dep.state in (DependencyState.OK, DependencyState.SKIPPED) for dep in dependencies
        )
        return ReadinessStatus(
            status="ready" if healthy else "not_ready",
            service=self._settings.app.name,
            version=self._settings.app.version,
            environment=self._settings.app.env.value,
            dependencies=list(dependencies),
        )

    async def _timed_check(self, name: str, check: Awaitable[object]) -> DependencyHealth:
        start = perf_counter()
        try:
            await asyncio.wait_for(check, timeout=_CHECK_TIMEOUT_SECONDS)
        except Exception as exc:
            elapsed = round((perf_counter() - start) * 1000, 2)
            log.warning("dependency_unhealthy", dependency=name, error=str(exc))
            return DependencyHealth(
                name=name,
                state=DependencyState.DOWN,
                detail=str(exc) or exc.__class__.__name__,
                latency_ms=elapsed,
            )
        elapsed = round((perf_counter() - start) * 1000, 2)
        return DependencyHealth(name=name, state=DependencyState.OK, latency_ms=elapsed)

    async def _check_minio(self) -> DependencyHealth:
        url = f"{self._settings.minio.url}/minio/health/live"
        start = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
                response = await client.get(url)
                response.raise_for_status()
        except Exception as exc:
            elapsed = round((perf_counter() - start) * 1000, 2)
            log.warning("dependency_unhealthy", dependency="minio", error=str(exc))
            return DependencyHealth(
                name="minio",
                state=DependencyState.DOWN,
                detail=str(exc) or exc.__class__.__name__,
                latency_ms=elapsed,
            )
        elapsed = round((perf_counter() - start) * 1000, 2)
        return DependencyHealth(name="minio", state=DependencyState.OK, latency_ms=elapsed)
