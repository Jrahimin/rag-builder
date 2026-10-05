"""Immutable review overlays for one sealed build; original chunks are never rewritten."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, UUIDPrimaryKeyMixin


class IndexScopeReview(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    __tablename__ = "index_scope_reviews"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["project_id", "build_id"],
            ["index_builds.project_id", "index_builds.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_revision_id"], ["source_metadata_revisions.id"], ondelete="RESTRICT"
        ),
        Index(
            "ix_index_scope_reviews_membership", "project_id", "build_id", "chunk_id", unique=True
        ),
    )
    build_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_generation: Mapped[int] = mapped_column(nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
