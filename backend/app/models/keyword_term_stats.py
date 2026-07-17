"""Keyword term statistics for BM25 scoring."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ProjectScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class KeywordTermStats(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """Document frequency of a term within a project and embedding set version."""

    __tablename__ = "keyword_term_stats"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "embedding_set_version",
            "term",
            name="uq_keyword_term_stats_project_esv_term",
        ),
        Index(
            "ix_keyword_term_stats_project_esv",
            "project_id",
            "embedding_set_version",
        ),
    )

    embedding_set_version: Mapped[int] = mapped_column(Integer, nullable=False)
    term: Mapped[str] = mapped_column(String(128), nullable=False)
    document_frequency: Mapped[int] = mapped_column(Integer, nullable=False)


class KeywordCollectionStats(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """Collection-level BM25 statistics for a project and embedding set version."""

    __tablename__ = "keyword_collection_stats"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "embedding_set_version",
            name="uq_keyword_collection_stats_project_esv",
        ),
    )

    embedding_set_version: Mapped[int] = mapped_column(Integer, nullable=False)
    total_documents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    total_chunks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    avg_doc_length: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0"),
    )
