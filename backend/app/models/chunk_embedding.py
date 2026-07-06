"""Chunk embedding ORM entity — project-scoped vectors stored as BYTEA."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKeyConstraint, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ProjectScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

EMBEDDING_SCHEMA_VERSION = 1


class ChunkEmbedding(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """Dense vector for a document chunk at a specific embedding set version."""

    __tablename__ = "chunk_embeddings"
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
            "provider",
            "model",
            name="uq_chunk_embeddings_chunk_esv_provider_model",
        ),
        Index("ix_chunk_embeddings_project_document", "project_id", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    embedding_set_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=EMBEDDING_SCHEMA_VERSION,
    )
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
