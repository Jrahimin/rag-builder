"""Safe corpus and immutable index lifecycle.

Revision ID: 0019_safe_corpus
Revises: 0018_evidence_quality
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_safe_corpus"
down_revision: str | None = "0018_evidence_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_runs", sa.Column("result", postgresql.JSONB(), nullable=True))
    op.drop_constraint("fk_job_runs_document_id_documents", "job_runs", type_="foreignkey")
    op.create_foreign_key("fk_job_runs_document_id_documents", "job_runs", "documents", ["document_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "index_builds",
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(32), server_default="building", nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("embedding_set_version", sa.Integer(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("corpus_fingerprint", sa.String(64), nullable=True),
        sa.Column("document_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("vector_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("keyword_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["job_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_builds")),
        sa.UniqueConstraint("job_id", name=op.f("uq_index_builds_job_id")),
    )
    op.create_index("ix_index_builds_project_created", "index_builds", ["project_id", "created_at", "id"])
    op.create_index("ix_index_builds_project_state", "index_builds", ["project_id", "state", "created_at"])
    op.create_table(
        "project_index_pointers",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("active_build_id", sa.Uuid(), nullable=True),
        sa.Column("previous_build_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["active_build_id"], ["index_builds.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_build_id"], ["index_builds.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_project_index_pointers")),
    )

    op.add_column("document_chunks", sa.Column("document_version", sa.Integer(), server_default=sa.text("1"), nullable=False))
    op.drop_constraint("uq_document_chunks_document_index", "document_chunks", type_="unique")
    op.create_unique_constraint("uq_document_chunks_document_version_index", "document_chunks", ["document_id", "document_version", "chunk_index"])
    artifact_tables = ("chunk_embeddings", "chunk_keyword_index", "keyword_term_stats", "keyword_collection_stats")
    for table in artifact_tables:
        op.add_column(table, sa.Column("index_build_id", sa.Uuid(), nullable=True))

    op.execute("""
        INSERT INTO index_builds (id, project_id, state, operation, embedding_set_version,
          configuration_hash, corpus_fingerprint, document_count, chunk_count,
          vector_count, keyword_count, manifest, validated_at, activated_at)
        SELECT gen_random_uuid(), p.id, 'active', 'migration',
          COALESCE((SELECT MAX(embedding_set_version) FROM chunk_embeddings WHERE project_id = p.id), 1),
          repeat('0', 64), repeat('0', 64),
          (SELECT COUNT(*) FROM documents WHERE project_id = p.id AND deleted_at IS NULL),
          (SELECT COUNT(*) FROM document_chunks WHERE project_id = p.id),
          (SELECT COUNT(*) FROM chunk_embeddings WHERE project_id = p.id),
          (SELECT COUNT(*) FROM chunk_keyword_index WHERE project_id = p.id),
          jsonb_build_object('migration_backfill', true), now(), now()
        FROM projects p
    """)
    op.execute("INSERT INTO project_index_pointers (project_id, active_build_id) SELECT project_id, id FROM index_builds WHERE operation = 'migration'")
    for table in artifact_tables:
        op.execute(f"UPDATE {table} a SET index_build_id = p.active_build_id FROM project_index_pointers p WHERE a.project_id = p.project_id")
        op.alter_column(table, "index_build_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_index_build_id_index_builds", table, "index_builds", ["index_build_id"], ["id"], ondelete="CASCADE")
        op.create_index(f"ix_{table}_index_build_id", table, ["index_build_id"])

    op.drop_constraint("uq_chunk_embeddings_chunk_esv_provider_model", "chunk_embeddings", type_="unique")
    op.create_unique_constraint("uq_chunk_embeddings_build_chunk_esv_provider_model", "chunk_embeddings", ["index_build_id", "chunk_id", "embedding_set_version", "provider", "model"])
    op.drop_constraint("uq_chunk_keyword_index_chunk_esv", "chunk_keyword_index", type_="unique")
    op.create_unique_constraint("uq_chunk_keyword_index_build_chunk_esv", "chunk_keyword_index", ["index_build_id", "chunk_id", "embedding_set_version"])
    op.drop_constraint("uq_keyword_term_stats_project_esv_term", "keyword_term_stats", type_="unique")
    op.create_unique_constraint("uq_keyword_term_stats_project_build_esv_term", "keyword_term_stats", ["project_id", "index_build_id", "embedding_set_version", "term"])
    op.drop_constraint("uq_keyword_collection_stats_project_esv", "keyword_collection_stats", type_="unique")
    op.create_unique_constraint("uq_keyword_collection_stats_project_build_esv", "keyword_collection_stats", ["project_id", "index_build_id", "embedding_set_version"])


def downgrade() -> None:
    raise RuntimeError("Phase 5 corpus lifecycle migration is intentionally irreversible")
