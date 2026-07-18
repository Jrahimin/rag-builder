"""Immutable project-scoped retrieval index build metadata."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKeyConstraint, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class IndexBuildState(StrEnum):
    BUILDING = "building"
    VALIDATED = "validated"
    ACTIVE = "active"
    RETAINED = "retained"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class IndexBuildOperation(StrEnum):
    INGEST = "ingest"
    REPROCESS = "reprocess"
    REEMBED = "reembed"
    REINDEX = "reindex"
    DELETE = "delete"
    PURGE = "purge"
    MIGRATION = "migration"


class IndexBuild(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """One complete vector+keyword corpus snapshot."""

    __tablename__ = "index_builds"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["job_id"], ["job_runs.id"], ondelete="SET NULL"),
        Index("ix_index_builds_project_created", "project_id", "created_at", "id"),
        Index("ix_index_builds_project_state", "project_id", "state", "created_at"),
    )

    job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, unique=True)
    state: Mapped[IndexBuildState] = mapped_column(
        Enum(
            IndexBuildState,
            name="index_build_state",
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=IndexBuildState.BUILDING,
        server_default=IndexBuildState.BUILDING.value,
    )
    operation: Mapped[IndexBuildOperation] = mapped_column(
        Enum(
            IndexBuildOperation,
            name="index_build_operation",
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    embedding_set_version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    corpus_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    vector_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    keyword_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectIndexPointer(Base, TimestampMixin):
    """The single authoritative active/rollback pointer for a Project."""

    __tablename__ = "project_index_pointers"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["active_build_id"], ["index_builds.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["previous_build_id"], ["index_builds.id"], ondelete="SET NULL"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    active_build_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    previous_build_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
