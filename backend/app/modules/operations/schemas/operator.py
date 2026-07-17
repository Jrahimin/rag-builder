"""Sanitized deployment-operator response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome
from app.platform.system.schemas import ReadinessStatus


class WorkerStatus(BaseModel):
    worker_id: str
    hostname: str
    process_id: int
    queue: str
    version: str
    started_at: datetime
    heartbeat_at: datetime
    heartbeat_age_seconds: float
    state: str


class WorkerOverview(BaseModel):
    available: bool
    active_count: int
    stale_after_seconds: int
    workers: list[WorkerStatus]
    detail: str | None = None


class JobMetrics(BaseModel):
    total: int
    by_state: dict[str, int]
    queued: int
    running: int
    retry_scheduled: int
    failures_24h: int
    retry_attempts: int
    oldest_queue_age_seconds: float | None
    pending_dispatches: int
    oldest_dispatch_age_seconds: float | None
    dispatch_attempts: int


class LatencyMetric(BaseModel):
    name: str
    count: int
    average_ms: float | None
    maximum_ms: float | None


class TokenUsageMetrics(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class CorpusMetrics(BaseModel):
    projects: int
    documents: int
    chunks: int
    storage_bytes: int


class MetricsSnapshot(BaseModel):
    generated_at: datetime
    jobs: JobMetrics
    job_latency: list[LatencyMetric]
    provider_generation_latency: list[LatencyMetric]
    retrieval_latency: LatencyMetric
    generation_latency: LatencyMetric
    token_usage: TokenUsageMetrics
    corpus: CorpusMetrics
    active_embedding_set_version: int


class ProviderConfiguration(BaseModel):
    backend: str
    model: str | None = None
    dimensions: int | None = None
    provider_version: str | None = None
    credential_configured: bool | None = None


class ConfigurationSnapshotSummary(BaseModel):
    project_id: uuid.UUID
    snapshot_id: uuid.UUID
    configuration_hash: str
    schema_version: int
    created_at: datetime


class ActiveConfiguration(BaseModel):
    environment: str
    runtime_profile: str
    application_version: str
    llm: ProviderConfiguration
    embedding: ProviderConfiguration
    reranker_backend: str
    ocr_backend: str
    ocr_enabled: bool
    storage_backend: str
    job_backend: str
    retrieval_strategy: str
    embedding_set_version: int
    recent_project_snapshots: list[ConfigurationSnapshotSummary]


class RecentFailure(BaseModel):
    job_id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID | None
    job_type: str
    stage: str
    failure_code: str
    failure_message: str
    attempt_count: int
    failed_at: datetime


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    event_type: AuditEventType
    actor_type: AuditActorType
    actor_id: str | None
    resource_type: str
    resource_id: uuid.UUID
    outcome: AuditOutcome
    detail: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class DependencyOverview(BaseModel):
    readiness: ReadinessStatus
    startup_profile: str
    startup_checked_at: datetime


class OperatorOverview(BaseModel):
    status: str
    dependencies: DependencyOverview
    workers: WorkerOverview
    metrics: MetricsSnapshot
    recent_failures: list[RecentFailure] = Field(default_factory=list)
