"""Append-only build quality receipts and relationship review provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261002_0039"
down_revision = "20261001_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_index_builds_project_id", "index_builds", ["project_id", "id"])
    op.create_table(
        "index_acceptances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("build_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.Column("artifact", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "build_id"],
            ["index_builds.project_id", "index_builds.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_index_acceptances_project_build",
        "index_acceptances",
        ["project_id", "build_id", "created_at"],
    )
    op.create_index(
        "ix_index_acceptances_project_hash",
        "index_acceptances",
        ["project_id", "artifact_hash"],
        unique=True,
    )
    op.execute("""CREATE FUNCTION reject_index_acceptance_update() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'index acceptance receipts are immutable'; END;
        $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER index_acceptance_immutable BEFORE UPDATE ON index_acceptances
        FOR EACH ROW EXECUTE FUNCTION reject_index_acceptance_update()""")
    op.add_column(
        "source_revision_relationships",
        sa.Column(
            "review_provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("source_revision_relationships", "review_provenance")
    op.drop_table("index_acceptances")
    op.drop_constraint("uq_index_builds_project_id", "index_builds", type_="unique")
    op.execute("DROP FUNCTION reject_index_acceptance_update()")
