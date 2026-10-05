"""Immutable scope review overlays and observed acceptance reports; preserve prior revisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261003_0042"
down_revision = "20261002_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, columns in (
        (
            "index_scope_reviews",
            [
                sa.Column("chunk_id", sa.Uuid(), nullable=False),
                sa.Column("source_revision_id", sa.Uuid(), nullable=False),
                sa.Column("source_generation", sa.Integer(), nullable=False),
                sa.Column("chunk_hash", sa.String(64), nullable=False),
                sa.Column("review_hash", sa.String(64), nullable=False),
                sa.Column("envelope", postgresql.JSONB(), nullable=False),
                sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
                sa.ForeignKeyConstraint(
                    ["source_revision_id"], ["source_metadata_revisions.id"], ondelete="RESTRICT"
                ),
            ],
        ),
        (
            "index_acceptance_reports",
            [
                sa.Column("report_hash", sa.String(64), nullable=False),
                sa.Column("report", postgresql.JSONB(), nullable=False),
            ],
        ),
    ):
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("build_id", sa.Uuid(), nullable=False),
            *columns,
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["project_id", "build_id"],
                ["index_builds.project_id", "index_builds.id"],
                ondelete="CASCADE",
            ),
        )
        op.execute(
            f"CREATE TRIGGER {name}_immutable BEFORE UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION reject_index_acceptance_update()"
        )
    op.create_index(
        "ix_index_scope_reviews_membership",
        "index_scope_reviews",
        ["project_id", "build_id", "chunk_id"],
        unique=True,
    )
    op.create_index(
        "ix_index_acceptance_reports_hash",
        "index_acceptance_reports",
        ["project_id", "build_id", "report_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("index_acceptance_reports")
    op.drop_table("index_scope_reviews")
