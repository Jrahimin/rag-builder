"""Knowledge-owned immutable source metadata and activation history."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, UUIDPrimaryKeyMixin


class SourceLifecycleStatus(StrEnum):
    UNSPECIFIED = "unspecified"
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class SourceRole(StrEnum):
    UNSPECIFIED = "unspecified"
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    REFERENCE = "reference"


class SourceRelationshipType(StrEnum):
    REPLACES = "replaces"
    MODIFIES = "modifies"


class SourceGroup(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Stable identity shared only by revisions of one logical source."""

    __tablename__ = "source_groups"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        Index("ix_source_groups_project_created", "project_id", "created_at", "id"),
    )

    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceMetadataRevision(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Immutable administrative metadata revision associated with a Document."""

    __tablename__ = "source_metadata_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["source_group_id"], ["source_groups.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "source_group_id",
            "revision_number",
            name="uq_source_metadata_group_revision_number",
        ),
        CheckConstraint("revision_number > 0", name="source_metadata_revision_positive"),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="source_metadata_effective_interval",
        ),
        Index(
            "ix_source_metadata_revisions_project_document_created",
            "project_id",
            "document_id",
            "created_at",
        ),
        Index(
            "ix_source_metadata_revisions_project_group_revision",
            "project_id",
            "source_group_id",
            "revision_number",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_group_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_label: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    lifecycle_status: Mapped[SourceLifecycleStatus] = mapped_column(
        Enum(
            SourceLifecycleStatus,
            name="source_lifecycle_status",
            native_enum=False,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=SourceLifecycleStatus.UNSPECIFIED,
        server_default=SourceLifecycleStatus.UNSPECIFIED.value,
    )
    source_role: Mapped[SourceRole] = mapped_column(
        Enum(
            SourceRole,
            name="source_role",
            native_enum=False,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=SourceRole.UNSPECIFIED,
        server_default=SourceRole.UNSPECIFIED.value,
    )
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceRevisionRelationship(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Immutable relation edge declared by a source metadata revision."""

    __tablename__ = "source_revision_relationships"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_revision_id"], ["source_metadata_revisions.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["target_revision_id"], ["source_metadata_revisions.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "source_revision_id",
            "target_revision_id",
            "relationship_type",
            name="uq_source_revision_relationship_edge",
        ),
        CheckConstraint(
            "source_revision_id <> target_revision_id",
            name="source_revision_relationship_not_self",
        ),
        Index(
            "ix_source_revision_relationships_project_source",
            "project_id",
            "source_revision_id",
        ),
        Index(
            "ix_source_revision_relationships_project_target",
            "project_id",
            "target_revision_id",
        ),
    )

    source_revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    target_revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    relationship_type: Mapped[SourceRelationshipType] = mapped_column(
        Enum(
            SourceRelationshipType,
            name="source_relationship_type",
            native_enum=False,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    target_provisions: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceActivationEvent(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Append-only mapping of a Document to metadata at a Project generation."""

    __tablename__ = "source_activation_events"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_revision_id"], ["source_metadata_revisions.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "project_id",
            "document_id",
            "generation",
            name="uq_source_activation_project_document_generation",
        ),
        CheckConstraint("generation > 0", name="source_activation_generation_positive"),
        Index(
            "ix_source_activation_events_project_generation",
            "project_id",
            "generation",
            "created_at",
        ),
        Index(
            "ix_source_activation_events_project_document_generation",
            "project_id",
            "document_id",
            "generation",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    activated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
