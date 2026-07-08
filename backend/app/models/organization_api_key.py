"""Organization API key ORM entity."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class OrganizationApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Named, rotatable API credential scoped to an Organization."""

    __tablename__ = "organization_api_keys"
    __table_args__ = (
        Index(
            "uq_org_api_keys_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_org_api_keys_key_hash", "key_hash", unique=True),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
