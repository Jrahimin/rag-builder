"""Transactional outbox rows for durable Taskiq dispatch."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class JobOutboxState(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"


class JobOutbox(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """A replayable intent to place a persisted job on Taskiq/Redis."""

    __tablename__ = "job_outbox"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["job_run_id"], ["job_runs.id"], ondelete="CASCADE"),
        Index("ix_job_outbox_pending", "state", "available_at", "created_at"),
        Index("ix_job_outbox_job", "project_id", "job_run_id", "created_at"),
    )

    job_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    state: Mapped[JobOutboxState] = mapped_column(
        Enum(JobOutboxState, name="job_outbox_state", native_enum=False, length=32),
        nullable=False,
        default=JobOutboxState.PENDING,
        server_default=JobOutboxState.PENDING.value,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    dispatch_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
