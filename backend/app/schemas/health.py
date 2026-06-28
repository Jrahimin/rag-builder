"""Schemas for health (liveness) and readiness endpoints."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DependencyState(StrEnum):
    """Health state of a single downstream dependency."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    SKIPPED = "skipped"


class DependencyHealth(BaseModel):
    """Readiness result for one downstream dependency."""

    name: str
    state: DependencyState
    detail: str | None = None
    latency_ms: float | None = None


class LivenessStatus(BaseModel):
    """Liveness probe payload - reports only that the process is running."""

    status: str = "ok"
    service: str
    version: str
    environment: str


class ReadinessStatus(BaseModel):
    """Readiness probe payload - aggregates downstream dependency health."""

    status: str
    service: str
    version: str
    environment: str
    dependencies: list[DependencyHealth]
