"""Durable project-scoped background job executions."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class JobType(StrEnum):
    DOCUMENT_PROCESS = "document.process"
    DOCUMENT_EMBED = "document.embed"
    DOCUMENT_INDEX = "document.index"
    EVALUATION_RUN = "evaluation.run"
    CORPUS_REEMBED = "corpus.reembed"
    CORPUS_REINDEX = "corpus.reindex"
    DOCUMENT_DELETE = "document.delete"
    DOCUMENT_PURGE = "document.purge"
    STORAGE_RECONCILE = "storage.reconcile"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobRun(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """One recoverable execution with lease, attempt, and failure state."""

    __tablename__ = "job_runs"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(
            ["configuration_snapshot_id"],
            ["job_configuration_snapshots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["retry_of_job_id"], ["job_runs.id"], ondelete="SET NULL"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_job_runs_project_idempotency"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="job_runs_progress_range"),
        CheckConstraint("attempt_count >= 0", name="job_runs_attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="job_runs_max_attempts_positive"),
        Index("ix_job_runs_project_created", "project_id", "created_at", "id"),
        Index("ix_job_runs_project_state", "project_id", "state", "created_at"),
        Index("ix_job_runs_recovery", "state", "lease_expires_at", "next_attempt_at"),
        Index("ix_job_runs_document", "project_id", "document_id", "created_at"),
    )

    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="job_type", native_enum=False, length=32),
        nullable=False,
    )
    state: Mapped[JobState] = mapped_column(
        Enum(JobState, name="job_state", native_enum=False, length=32),
        nullable=False,
        default=JobState.QUEUED,
        server_default=JobState.QUEUED.value,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    configuration_snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
