"""Minimal, revocable Super Admin browser session persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AdminSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One refresh-token-backed browser session; access JWTs reference ``id``."""

    __tablename__ = "admin_sessions"
    __table_args__ = (
        Index("ix_admin_sessions_user_id", "admin_user_id"),
        Index("ix_admin_sessions_refresh_token_hash", "refresh_token_hash", unique=True),
    )

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
