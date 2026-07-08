"""organizations, api keys, and project organization_id

Revision ID: 0014_organizations_auth
Revises: 0013_doc_extraction_meta
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_organizations_auth"
down_revision: str | None = "0013_doc_extraction_meta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_index("ix_organizations_deleted_at", "organizations", ["deleted_at"], unique=False)
    op.create_index("ix_organizations_is_active", "organizations", ["is_active"], unique=False)

    op.create_table(
        "organization_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_api_keys_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_api_keys")),
    )
    op.create_index(
        "ix_organization_api_keys_organization_id",
        "organization_api_keys",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_org_api_keys_key_hash",
        "organization_api_keys",
        ["key_hash"],
        unique=True,
    )
    op.create_index(
        "uq_org_api_keys_org_name",
        "organization_api_keys",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.add_column("projects", sa.Column("organization_id", sa.Uuid(), nullable=True))
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"], unique=False)

    op.execute(
        sa.text(
            """
            INSERT INTO organizations (id, name, description, is_active)
            VALUES (:id, 'Default', 'Default organization for existing projects', true)
            """
        ).bindparams(id=_DEFAULT_ORG_ID)
    )
    op.execute(
        sa.text(
            "UPDATE projects SET organization_id = :org_id WHERE organization_id IS NULL"
        ).bindparams(org_id=_DEFAULT_ORG_ID)
    )

    op.alter_column("projects", "organization_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_projects_organization_id_organizations"),
        "projects",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_index("uq_projects_name", table_name="projects")
    op.create_index(
        "uq_projects_org_name",
        "projects",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_projects_org_name", table_name="projects")
    op.create_index(
        "uq_projects_name",
        "projects",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_constraint(
        op.f("fk_projects_organization_id_organizations"),
        "projects",
        type_="foreignkey",
    )
    op.drop_index("ix_projects_organization_id", table_name="projects")
    op.drop_column("projects", "organization_id")

    op.drop_index("uq_org_api_keys_org_name", table_name="organization_api_keys")
    op.drop_index("ix_org_api_keys_key_hash", table_name="organization_api_keys")
    op.drop_index(
        "ix_organization_api_keys_organization_id",
        table_name="organization_api_keys",
    )
    op.drop_table("organization_api_keys")

    op.drop_index("ix_organizations_is_active", table_name="organizations")
    op.drop_index("ix_organizations_deleted_at", table_name="organizations")
    op.drop_table("organizations")
