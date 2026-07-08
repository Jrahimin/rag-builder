"""Organization ORM entity — tenant and authentication boundary."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ActiveStatusMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin, ActiveStatusMixin, SoftDeleteMixin):
    """Tenant/customer; must be active for API access."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
