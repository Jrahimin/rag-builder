"""Phase 2 Operator onboarding and immutable source lifecycle.

Revision ID: 0024_phase2_sources
Revises: 0023_phase1_policy
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_phase2_sources"
down_revision: str | None = "0023_phase1_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_lifecycle_status = sa.Enum(
    "unspecified",
    "draft",
    "active",
    "retired",
    name="source_lifecycle_status",
    native_enum=False,
)
source_role = sa.Enum(
    "unspecified",
    "primary",
    "supporting",
    "reference",
    name="source_role",
    native_enum=False,
)
source_relationship_type = sa.Enum(
    "replaces",
    "modifies",
    name="source_relationship_type",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column(
        "organization_api_keys",
        sa.Column("created_by", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "organization_api_keys",
        sa.Column("rotated_from_key_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_org_api_keys_rotated_from",
        "organization_api_keys",
        "organization_api_keys",
        ["rotated_from_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_organization_api_keys_rotated_from_key_id",
        "organization_api_keys",
        ["rotated_from_key_id"],
    )

    source_lifecycle_status.create(op.get_bind(), checkfirst=True)
    source_role.create(op.get_bind(), checkfirst=True)
    source_relationship_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "source_groups",
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_groups")),
    )
    op.create_index(
        "ix_source_groups_project_created",
        "source_groups",
        ["project_id", "created_at", "id"],
    )

    op.create_table(
        "source_metadata_revisions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("source_group_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_label", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source_type", sa.String(length=128), nullable=True),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "lifecycle_status",
            source_lifecycle_status,
            server_default="unspecified",
            nullable=False,
        ),
        sa.Column(
            "source_role",
            source_role,
            server_default="unspecified",
            nullable=False,
        ),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name=op.f("ck_source_metadata_revisions_source_metadata_effective_interval"),
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name=op.f("ck_source_metadata_revisions_source_metadata_revision_positive"),
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_group_id"], ["source_groups.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_metadata_revisions")),
        sa.UniqueConstraint(
            "source_group_id",
            "revision_number",
            name="uq_source_metadata_group_revision_number",
        ),
    )
    op.create_index(
        "ix_source_metadata_revisions_project_document_created",
        "source_metadata_revisions",
        ["project_id", "document_id", "created_at"],
    )
    op.create_index(
        "ix_source_metadata_revisions_project_group_revision",
        "source_metadata_revisions",
        ["project_id", "source_group_id", "revision_number"],
    )

    op.create_table(
        "source_revision_relationships",
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("target_revision_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", source_relationship_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "source_revision_id <> target_revision_id",
            name=op.f("ck_source_revision_relationships_source_revision_relationship_not_self"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["source_metadata_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_revision_id"],
            ["source_metadata_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_revision_relationships")),
        sa.UniqueConstraint(
            "source_revision_id",
            "target_revision_id",
            "relationship_type",
            name="uq_source_revision_relationship_edge",
        ),
    )
    op.create_index(
        "ix_source_revision_relationships_project_source",
        "source_revision_relationships",
        ["project_id", "source_revision_id"],
    )
    op.create_index(
        "ix_source_revision_relationships_project_target",
        "source_revision_relationships",
        ["project_id", "target_revision_id"],
    )

    op.create_table(
        "source_activation_events",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("activated_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "generation > 0",
            name=op.f("ck_source_activation_events_source_activation_generation_positive"),
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["source_metadata_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_activation_events")),
        sa.UniqueConstraint(
            "project_id",
            "document_id",
            "generation",
            name="uq_source_activation_project_document_generation",
        ),
    )
    op.create_index(
        "ix_source_activation_events_project_generation",
        "source_activation_events",
        ["project_id", "generation", "created_at"],
    )
    op.create_index(
        "ix_source_activation_events_project_document_generation",
        "source_activation_events",
        ["project_id", "document_id", "generation"],
    )

    _backfill_neutral_source_metadata()


def _backfill_neutral_source_metadata() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, project_id, filename, content_sha256
            FROM documents
            ORDER BY project_id, id
            """
        )
    ).mappings()
    projects_with_documents: set[uuid.UUID] = set()
    for row in rows:
        projects_with_documents.add(row["project_id"])
        group_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        connection.execute(
            sa.text(
                """
                INSERT INTO source_groups (id, project_id, created_by)
                VALUES (:id, :project_id, 'migration')
                """
            ),
            {"id": group_id, "project_id": row["project_id"]},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO source_metadata_revisions (
                    id, project_id, document_id, source_group_id, revision_number,
                    revision_label, title, lifecycle_status, source_role,
                    change_reason, created_by, content_hash
                ) VALUES (
                    :id, :project_id, :document_id, :source_group_id, 1,
                    'Legacy import', :title, 'unspecified', 'unspecified',
                    'Phase 2 neutral metadata backfill', 'migration', :content_hash
                )
                """
            ),
            {
                "id": revision_id,
                "project_id": row["project_id"],
                "document_id": row["id"],
                "source_group_id": group_id,
                "title": row["filename"],
                "content_hash": row["content_sha256"],
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO source_activation_events (
                    id, project_id, document_id, source_revision_id, generation,
                    activated_by, reason
                ) VALUES (
                    :id, :project_id, :document_id, :source_revision_id, 1,
                    'migration', 'Phase 2 initial Project source generation'
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "project_id": row["project_id"],
                "document_id": row["id"],
                "source_revision_id": revision_id,
            },
        )
    if projects_with_documents:
        for project_id in projects_with_documents:
            connection.execute(
                sa.text(
                    """
                    UPDATE projects
                    SET source_metadata_generation = GREATEST(source_metadata_generation, 1)
                    WHERE id = :project_id
                    """
                ),
                {"project_id": project_id},
            )


def downgrade() -> None:
    raise RuntimeError("Phase 2 source metadata migration is intentionally irreversible")
