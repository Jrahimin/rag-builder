"""Upgrade the retrieval embedding contract to 1024 dimensions.

Revision ID: 0026_multilingual_embeddings
Revises: 0025_phase3_usage
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]

revision: str = "0026_multilingual_embeddings"
down_revision: str | None = "0025_phase3_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HNSW_INDEX = "ix_chunk_embeddings_embedding_hnsw_cosine"


def upgrade() -> None:
    _reset_retrieval_artifacts()
    _replace_vector_column_dimension(1024)


def downgrade() -> None:
    _reset_retrieval_artifacts()
    _replace_vector_column_dimension(384)


def _reset_retrieval_artifacts() -> None:
    """Invalidate every build before changing the deployment-wide vector contract."""
    op.execute(
        sa.text(
            """
            UPDATE documents
            SET status = 'CHUNKED', error_message = NULL, updated_at = now()
            WHERE id IN (
                SELECT document_id FROM chunk_embeddings
                UNION
                SELECT document_id FROM chunk_keyword_index
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM project_index_pointers"))
    for table_name in (
        "chunk_embeddings",
        "chunk_keyword_index",
        "keyword_term_stats",
        "keyword_collection_stats",
    ):
        op.execute(sa.text(f"DELETE FROM {table_name}"))
    op.execute(sa.text("DELETE FROM index_builds"))


def _replace_vector_column_dimension(dimensions: int) -> None:
    # HNSW indexes cannot be altered in place. The concurrent operations run
    # outside Alembic's transaction to avoid blocking the database catalog.
    with op.get_context().autocommit_block():
        op.drop_index(
            _HNSW_INDEX,
            table_name="chunk_embeddings",
            postgresql_concurrently=True,
        )
    op.drop_column(
        "chunk_embeddings",
        "embedding",
    )
    op.add_column(
        "chunk_embeddings",
        sa.Column("embedding", Vector(dimensions), nullable=False),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            _HNSW_INDEX,
            "chunk_embeddings",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_concurrently=True,
        )
