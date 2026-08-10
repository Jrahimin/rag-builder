"""Human platform administrator entity.

This is deliberately separate from organizations and API keys: it represents
the owner operating the platform, not an application calling the product API.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ActiveStatusMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AdminRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"


class AdminUser(Base, UUIDPrimaryKeyMixin, TimestampMixin, ActiveStatusMixin):
    """A human operator allowed to administer this APE deployment."""

    __tablename__ = "admin_users"
    __table_args__ = (Index("uq_admin_users_email", "email", unique=True),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AdminRole.SUPER_ADMIN.value
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
