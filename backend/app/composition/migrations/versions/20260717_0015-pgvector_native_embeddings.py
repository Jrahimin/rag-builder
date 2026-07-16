"""migrate chunk embeddings from packed BYTEA to native pgvector

Revision ID: 0015_pgvector_embeddings
Revises: 0014_organizations_auth
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]

from app.core.config import get_settings

revision: str = "0015_pgvector_embeddings"
down_revision: str | None = "0014_organizations_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dimensions = get_settings().embedding.dimensions
    try:
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as exc:
        message = (
            "pgvector is required but the PostgreSQL vector extension could not be "
            "created. Install pgvector and grant this migration role CREATE privilege, "
            "or ask the platform operator to run CREATE EXTENSION vector."
        )
        raise RuntimeError(message) from exc

    # Packed float bytes are deliberately not converted in SQL. Documents with
    # pre-cutover embeddings return to the stable re-embedding entry state.
    op.execute(
        sa.text(
            """
            UPDATE documents
            SET status = 'CHUNKED', error_message = NULL, updated_at = now()
            WHERE id IN (SELECT DISTINCT document_id FROM chunk_embeddings)
            """
        )
    )
    op.execute(sa.text("DELETE FROM chunk_embeddings"))
    op.drop_column("chunk_embeddings", "vector")
    op.add_column(
        "chunk_embeddings",
        sa.Column("embedding", Vector(dimensions), nullable=False),
    )
    op.create_index(
        "ix_chunk_embeddings_semantic_scope",
        "chunk_embeddings",
        ["project_id", "embedding_set_version", "provider", "model", "document_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_metadata_gin",
        "document_chunks",
        ["metadata"],
        unique=False,
        postgresql_using="gin",
    )

    # CREATE INDEX CONCURRENTLY cannot execute in Alembic's migration transaction.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_chunk_embeddings_embedding_hnsw_cosine",
            "chunk_embeddings",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    # Native vectors cannot be converted back to the legacy packed format
    # safely. Mirror the upgrade recovery policy and require re-embedding.
    op.execute(
        sa.text(
            """
            UPDATE documents
            SET status = 'CHUNKED', error_message = NULL, updated_at = now()
            WHERE id IN (SELECT DISTINCT document_id FROM chunk_embeddings)
            """
        )
    )
    op.execute(sa.text("DELETE FROM chunk_embeddings"))
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_chunk_embeddings_embedding_hnsw_cosine",
            table_name="chunk_embeddings",
            postgresql_concurrently=True,
        )
    op.drop_index("ix_document_chunks_metadata_gin", table_name="document_chunks")
    op.drop_index("ix_chunk_embeddings_semantic_scope", table_name="chunk_embeddings")
    op.drop_column("chunk_embeddings", "embedding")
    op.add_column(
        "chunk_embeddings",
        sa.Column("vector", sa.LargeBinary(), nullable=False),
    )
