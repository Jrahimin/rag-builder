"""Tokenizer term index and source-attested modification effects."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "20260930_0034"
down_revision = "20260908_0033"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_index("ix_chunk_keyword_index_term_frequencies", "chunk_keyword_index", ["term_frequencies"], postgresql_using="gin")
    op.add_column("source_revision_relationships", sa.Column("provision_effect", sa.String(32), nullable=False, server_default="unknown"))
    op.add_column("source_revision_relationships", sa.Column("replacement_scope_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("source_revision_relationships", sa.Column("supporting_spans", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))

def downgrade() -> None:
    op.drop_column("source_revision_relationships", "supporting_spans")
    op.drop_column("source_revision_relationships", "replacement_scope_verified")
    op.drop_column("source_revision_relationships", "provision_effect")
    op.drop_index("ix_chunk_keyword_index_term_frequencies", table_name="chunk_keyword_index")
