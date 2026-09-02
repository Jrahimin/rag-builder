"""version Project configuration, snapshots, and index artifact identity

Revision ID: 0029_config_phase1
Revises: 0028_scoped_modifies
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_config_phase1"
down_revision: str | None = "0028_scoped_modifies"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "project_ai_config_revisions",
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "project_ai_config_revisions",
        sa.Column("source", sa.String(length=64), server_default="legacy_v1", nullable=False),
    )
    op.add_column(
        "conversation_config_snapshots",
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "conversation_config_snapshots",
        sa.Column("resolution_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conversation_config_snapshots",
        sa.Column(
            "structured_origins",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "conversation_config_snapshots",
        sa.Column(
            "invariants",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("index_builds", sa.Column("artifact_fingerprint_version", sa.Integer()))
    op.add_column("index_builds", sa.Column("artifact_fingerprint", sa.String(length=64)))


def downgrade() -> None:
    op.drop_column("index_builds", "artifact_fingerprint")
    op.drop_column("index_builds", "artifact_fingerprint_version")
    op.drop_column("conversation_config_snapshots", "invariants")
    op.drop_column("conversation_config_snapshots", "structured_origins")
    op.drop_column("conversation_config_snapshots", "resolution_fingerprint")
    op.drop_column("conversation_config_snapshots", "schema_version")
    op.drop_column("project_ai_config_revisions", "source")
    op.drop_column("project_ai_config_revisions", "schema_version")
