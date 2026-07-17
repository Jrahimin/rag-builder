"""Chunk keyword index ORM entity — BM25/FTS rows per chunk."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ProjectScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ChunkKeywordIndex(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """Keyword-searchable snapshot of a chunk at a specific embedding set version."""

    __tablename__ = "chunk_keyword_index"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "chunk_id",
            "embedding_set_version",
            name="uq_chunk_keyword_index_chunk_esv",
        ),
        Index("ix_chunk_keyword_index_project_esv", "project_id", "embedding_set_version"),
        Index("ix_chunk_keyword_index_search_vector", "search_vector", postgresql_using="gin"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    embedding_set_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    term_frequencies: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)
