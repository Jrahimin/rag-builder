"""add evidence quality datasets, runs, and grounded message output

Revision ID: 0018_evidence_quality
Revises: 0017_operator_audit
Create Date: 2026-07-18 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_evidence_quality"
down_revision: str | None = "0017_operator_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "claims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("messages", sa.Column("grounded", sa.Boolean(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("insufficient_evidence_reason", sa.String(length=64), nullable=True),
    )

    op.create_table(
        "evaluation_datasets",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dataset_hash", sa.String(length=64), nullable=False),
        sa.Column("cases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_datasets")),
        sa.UniqueConstraint(
            "project_id",
            "name",
            "version",
            name="uq_evaluation_datasets_project_name_version",
        ),
    )
    op.create_index(
        "ix_evaluation_datasets_project_created",
        "evaluation_datasets",
        ["project_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "case_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "regressions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "failed_cases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reranker_comparison",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["job_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
        sa.UniqueConstraint(
            "project_id",
            "job_id",
            name="uq_evaluation_runs_project_job",
        ),
    )
    op.create_index(
        "ix_evaluation_runs_project_created",
        "evaluation_runs",
        ["project_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_runs_dataset_created",
        "evaluation_runs",
        ["project_id", "dataset_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_runs_dataset_created", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_project_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluation_datasets_project_created", table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")
    op.drop_column("messages", "insufficient_evidence_reason")
    op.drop_column("messages", "grounded")
    op.drop_column("messages", "claims")
