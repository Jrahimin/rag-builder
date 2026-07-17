"""Document ORM entity — project-scoped uploaded file metadata."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import BigInteger, Enum, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ProjectScopedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class DocumentStatus(StrEnum):
    """Ingestion lifecycle — full enum defined upfront; phases activate subsets."""

    UPLOADED = "uploaded"
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    CHUNKED = "chunked"
    FAILED = "failed"
    EMBEDDING = "embedding"
    EMBEDDED = "embedded"
    INDEXING = "indexing"
    READY = "ready"


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, ProjectScopedMixin):
    """Uploaded file owned by a Project — bytes live in object storage."""

    __tablename__ = "documents"
    job_id: ClassVar[uuid.UUID | None] = None
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        Index(
            "uq_documents_project_content_sha256",
            "project_id",
            "content_sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_documents_project_id_status", "project_id", "status"),
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", native_enum=False),
        nullable=False,
        default=DocumentStatus.UPLOADED,
        server_default=DocumentStatus.UPLOADED.value,
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default=text("1"))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_parser: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parse_quality_score: Mapped[float | None] = mapped_column(nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int | None] = mapped_column(nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language_confidence: Mapped[float | None] = mapped_column(nullable=True)
    ocr_lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parsed_text_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
