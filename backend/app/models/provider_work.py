"""Project-scoped reusable vectors and durable provider billing attempts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class EmbeddingCache(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "embedding_cache"
    __table_args__ = (
        UniqueConstraint("project_id", "cache_key", name="uq_embedding_cache_project_key"),
        Index("ix_embedding_cache_expiry", "expires_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    cache_key: Mapped[str] = mapped_column(String(64))
    identity: Mapped[dict[str, Any]] = mapped_column(JSONB)
    vector: Mapped[list[float]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderUsageAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_usage_attempts"
    __table_args__ = (
        Index("ix_provider_usage_project_created", "project_id", "created_at"),
        Index("ix_provider_usage_reference", "project_id", "reference"),
    )

    # Retain billing history after project purge; no source text or credentials.
    project_id: Mapped[uuid.UUID] = mapped_column()
    reference: Mapped[str] = mapped_column(String(128))
    workload: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="reserved")
    price_version: Mapped[str] = mapped_column(String(64))
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger)
    billed_micro_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    billed_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    search_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
