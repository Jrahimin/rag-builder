"""Persisted output from one reproducible quality run."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, UUIDPrimaryKeyMixin


class EvaluationRun(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """One append-only evaluation result linked to a durable execution job."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["dataset_id"], ["evaluation_datasets.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["job_id"], ["job_runs.id"], ondelete="RESTRICT"),
        UniqueConstraint("project_id", "job_id", name="uq_evaluation_runs_project_job"),
        Index("ix_evaluation_runs_project_created", "project_id", "created_at", "id"),
        Index("ix_evaluation_runs_dataset_created", "project_id", "dataset_id", "created_at"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    case_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    regressions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    failed_cases: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    reranker_comparison: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
