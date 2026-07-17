"""Deployment-level health and readiness orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from time import perf_counter

from app.core.config import Settings
from app.core.logging import get_logger
from app.platform.db.session import Database
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.system.schemas import (
    DependencyHealth,
    DependencyState,
    LivenessStatus,
    PreflightStatus,
    ReadinessStatus,
)

log = get_logger(__name__)


class HealthService:
    """Aggregates liveness and readiness across platform dependencies."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        redis: RedisConnectivity,
        storage: BaseStorageProvider,
        preflight: PreflightStatus,
    ) -> None:
        self._settings = settings
        self._database = database
        self._redis = redis
        self._storage = storage
        self._preflight = preflight

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
            self._timed_check("object_storage", self._storage.check()),
        )
        provider_checks = [
            check.model_copy(update={"cached": True})
            for check in self._preflight.checks
            if check.name.endswith("_provider")
        ]
        all_dependencies = [*dependencies, *provider_checks]
        healthy = all(
            dep.state in (DependencyState.OK, DependencyState.SKIPPED) for dep in all_dependencies
        )
        return ReadinessStatus(
            status="ready" if healthy else "not_ready",
            service=self._settings.app.name,
            version=self._settings.app.version,
            environment=self._settings.app.env.value,
            dependencies=list(all_dependencies),
        )

    async def _timed_check(self, name: str, check: Awaitable[object]) -> DependencyHealth:
        start = perf_counter()
        try:
            await asyncio.wait_for(
                check,
                timeout=self._settings.runtime.dependency_timeout_seconds,
            )
        except Exception as exc:
            elapsed = round((perf_counter() - start) * 1000, 2)
            log.warning("dependency_unhealthy", dependency=name, error=str(exc))
            return DependencyHealth(
                name=name,
                state=DependencyState.DOWN,
                detail=f"{type(exc).__name__}: dependency check failed; see logs.",
                latency_ms=elapsed,
                action=f"Verify {name} connectivity and deployment configuration.",
            )
        elapsed = round((perf_counter() - start) * 1000, 2)
        return DependencyHealth(name=name, state=DependencyState.OK, latency_ms=elapsed)
