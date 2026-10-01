"""Isolate structural reprocessing from active document chunks.
Revision ID: 0035
Revises: 0034
"""
from alembic import op
import sqlalchemy as sa
revision = "20260930_0035"
down_revision = "20260930_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("chunk_generation_id", sa.Uuid(), nullable=True))
    op.add_column("document_chunks", sa.Column("generation_id", sa.Uuid(), nullable=True))
    op.drop_constraint("uq_document_chunks_document_version_index", "document_chunks", type_="unique")
    op.create_unique_constraint("uq_document_chunks_document_version_index", "document_chunks", ["document_id", "document_version", "chunk_index", "generation_id"], postgresql_nulls_not_distinct=True)


def downgrade() -> None:
    # Private snapshots must be removed before downgrade; never silently delete evidence.
    op.drop_constraint("uq_document_chunks_document_version_index", "document_chunks", type_="unique")
    op.create_unique_constraint("uq_document_chunks_document_version_index", "document_chunks", ["document_id", "document_version", "chunk_index"])
    op.drop_column("document_chunks", "generation_id")
    op.drop_column("documents", "chunk_generation_id")
