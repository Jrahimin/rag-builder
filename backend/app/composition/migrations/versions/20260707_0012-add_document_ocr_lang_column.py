"""add ocr_lang to documents

Revision ID: 0012_document_ocr_lang
Revises: 0011_language_confidence
Create Date: 2026-07-07 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_document_ocr_lang"
down_revision: str | None = "0011_language_confidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("ocr_lang", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "ocr_lang")
