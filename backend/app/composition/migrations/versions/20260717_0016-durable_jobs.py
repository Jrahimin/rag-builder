"""add durable jobs, immutable config snapshots, and transactional outbox

Revision ID: 0016_durable_jobs
Revises: 0015_pgvector_embeddings
Create Date: 2026-07-17 12:00:00.000000
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings
from app.platform.jobs.configuration import build_job_configuration

revision: str = "0016_durable_jobs"
down_revision: str | None = "0015_pgvector_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_configuration_snapshots",
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_configuration_snapshots")),
        sa.UniqueConstraint(
            "project_id",
            "configuration_hash",
            name="uq_job_configuration_snapshots_project_hash",
        ),
    )
    op.create_index(
        "ix_job_configuration_snapshots_project_created",
        "job_configuration_snapshots",
        ["project_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "job_runs",
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("configuration_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("retry_of_job_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("failure_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_job_runs_job_runs_attempt_count_nonnegative")),
        sa.CheckConstraint("max_attempts >= 1", name=op.f("ck_job_runs_job_runs_max_attempts_positive")),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name=op.f("ck_job_runs_job_runs_progress_range")),
        sa.ForeignKeyConstraint(["configuration_snapshot_id"], ["job_configuration_snapshots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["retry_of_job_id"], ["job_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_job_runs_project_idempotency"),
    )
    op.create_index("ix_job_runs_document", "job_runs", ["project_id", "document_id", "created_at"], unique=False)
    op.create_index("ix_job_runs_project_created", "job_runs", ["project_id", "created_at", "id"], unique=False)
    op.create_index("ix_job_runs_project_state", "job_runs", ["project_id", "state", "created_at"], unique=False)
    op.create_index("ix_job_runs_recovery", "job_runs", ["state", "lease_expires_at", "next_attempt_at"], unique=False)

    op.create_table(
        "job_outbox",
        sa.Column("job_run_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["job_run_id"], ["job_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_outbox")),
    )
    op.create_index("ix_job_outbox_job", "job_outbox", ["project_id", "job_run_id", "created_at"], unique=False)
    op.create_index("ix_job_outbox_pending", "job_outbox", ["state", "available_at", "created_at"], unique=False)
    _backfill_interrupted_document_work()


def _backfill_interrupted_document_work() -> None:
    """Convert pre-Phase-1 in-flight lifecycle states into durable jobs."""
    connection = op.get_bind()
    settings = get_settings()
    configuration = build_job_configuration(settings)
    configuration_json = json.dumps(configuration.model_dump(mode="json"), sort_keys=True)
    rows = connection.execute(
        sa.text(
            """
            SELECT id, project_id, status, version
            FROM documents
            WHERE deleted_at IS NULL
              AND status IN (
                'uploaded', 'queued', 'parsing', 'chunking',
                'chunked', 'embedding', 'embedded', 'indexing'
              )
            ORDER BY project_id, id
            """
        )
    ).mappings()
    snapshot_ids: dict[uuid.UUID, uuid.UUID] = {}
    for row in rows:
        job_type = _backfill_job_type(row["status"], settings.retrieval.auto_embed, settings.retrieval.auto_index)
        if job_type is None:
            continue
        project_id = row["project_id"]
        snapshot_id = snapshot_ids.get(project_id)
        if snapshot_id is None:
            snapshot_id = uuid.uuid4()
            snapshot_ids[project_id] = snapshot_id
            connection.execute(
                sa.text(
                    """
                    INSERT INTO job_configuration_snapshots (
                        id, project_id, schema_version, configuration_hash, configuration
                    ) VALUES (
                        :id, :project_id, :schema_version, :configuration_hash,
                        CAST(:configuration AS jsonb)
                    )
                    """
                ),
                {
                    "id": snapshot_id,
                    "project_id": project_id,
                    "schema_version": configuration.schema_version,
                    "configuration_hash": configuration.digest(),
                    "configuration": configuration_json,
                },
            )
        run_id = uuid.uuid4()
        payload = {
            "document_version": row["version"],
            "embedding_set_version": settings.retrieval.embedding_set_version,
            "migration_backfill": True,
        }
        connection.execute(
            sa.text(
                """
                INSERT INTO job_runs (
                    id, project_id, job_type, state, stage, progress, payload,
                    idempotency_key, document_id, configuration_snapshot_id,
                    attempt_count, max_attempts
                ) VALUES (
                    :id, :project_id, :job_type, 'queued', 'queued', 0,
                    CAST(:payload AS jsonb), :idempotency_key, :document_id,
                    :configuration_snapshot_id, 0, :max_attempts
                )
                """
            ),
            {
                "id": run_id,
                "project_id": project_id,
                "job_type": job_type,
                "payload": json.dumps(payload, sort_keys=True),
                "idempotency_key": (
                    f"migration:{job_type}:{project_id}:{row['id']}:"
                    f"v{row['version']}:cfg{configuration.digest()[:16]}"
                ),
                "document_id": row["id"],
                "configuration_snapshot_id": snapshot_id,
                "max_attempts": settings.jobs.max_attempts,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO job_outbox (id, project_id, job_run_id, state)
                VALUES (:id, :project_id, :job_run_id, 'pending')
                """
            ),
            {"id": uuid.uuid4(), "project_id": project_id, "job_run_id": run_id},
        )
        if job_type == "document.process":
            connection.execute(
                sa.text("UPDATE documents SET status = 'queued' WHERE id = :document_id"),
                {"document_id": row["id"]},
            )


def _backfill_job_type(status: str, auto_embed: bool, auto_index: bool) -> str | None:
    if status in {"uploaded", "queued", "parsing", "chunking"}:
        return "document.process"
    if status == "embedding" or (status == "chunked" and auto_embed):
        return "document.embed"
    if status == "indexing" or (status == "embedded" and auto_index):
        return "document.index"
    return None


def downgrade() -> None:
    op.drop_index("ix_job_outbox_pending", table_name="job_outbox")
    op.drop_index("ix_job_outbox_job", table_name="job_outbox")
    op.drop_table("job_outbox")
    op.drop_index("ix_job_runs_recovery", table_name="job_runs")
    op.drop_index("ix_job_runs_project_state", table_name="job_runs")
    op.drop_index("ix_job_runs_project_created", table_name="job_runs")
    op.drop_index("ix_job_runs_document", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_index("ix_job_configuration_snapshots_project_created", table_name="job_configuration_snapshots")
    op.drop_table("job_configuration_snapshots")
