"""Project ORM entity — central aggregate root and isolation boundary."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ActiveStatusMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin, ActiveStatusMixin, SoftDeleteMixin):
    """Deployment-level aggregate root; scoped by ``organization_id``."""

    __tablename__ = "projects"
    __table_args__ = (
        Index(
            "uq_projects_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ownership_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    active_ai_config_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_ai_config_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    source_metadata_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
