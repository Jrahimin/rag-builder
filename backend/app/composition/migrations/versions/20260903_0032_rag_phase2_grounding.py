"""strip retired chat grounding keys from snapshots

Revision ID: 0032_rag_phase2_grounding
Revises: 0031_rag_phase1_reset
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings
from app.platform.config.legacy_reset import (
    conversation_invariants,
    reset_job_snapshot_configuration,
    reset_snapshot_configuration,
)
from app.platform.config.project_ai import stable_hash
from app.platform.jobs.contracts import JobConfiguration

revision: str = "0032_rag_phase2_grounding"
down_revision: str | None = "0031_rag_phase1_reset"
branch_labels: str | None = None
depends_on: str | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_conversation_snapshots = sa.table(
    "conversation_config_snapshots",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("schema_version", sa.Integer()),
    sa.column("configuration_hash", sa.String()),
    sa.column("resolution_fingerprint", sa.String()),
    sa.column("configuration", _JSONB),
    sa.column("provenance", _JSONB),
    sa.column("origins", _JSONB),
    sa.column("invariants", _JSONB),
)
_job_snapshots = sa.table(
    "job_configuration_snapshots",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("project_id", postgresql.UUID(as_uuid=True)),
    sa.column("configuration_hash", sa.String()),
    sa.column("configuration", _JSONB),
)
_job_runs = sa.table(
    "job_runs",
    sa.column("configuration_snapshot_id", postgresql.UUID(as_uuid=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    settings = get_settings()

    for row in bind.execute(sa.select(_conversation_snapshots)).mappings():
        cleaned = reset_snapshot_configuration(dict(row["configuration"]))
        invariants = conversation_invariants(cleaned)
        digest = stable_hash(cleaned)
        fingerprint = stable_hash(
            {
                "schema_version": row["schema_version"],
                "effective_value_hash": digest,
                "origins": row["origins"],
                "provenance": row["provenance"],
                "invariants": invariants,
            }
        )
        bind.execute(
            _conversation_snapshots.update()
            .where(_conversation_snapshots.c.id == row["id"])
            .values(
                configuration=cleaned,
                invariants=invariants,
                configuration_hash=digest,
                resolution_fingerprint=fingerprint,
            )
        )

    grouped: defaultdict[tuple[uuid.UUID, str], list[tuple[Any, dict[str, Any]]]]
    grouped = defaultdict(list)
    for row in bind.execute(sa.select(_job_snapshots)).mappings():
        cleaned = reset_job_snapshot_configuration(settings, dict(row["configuration"]))
        digest = JobConfiguration.model_validate(cleaned).output_digest()
        grouped[(row["project_id"], digest)].append((row, cleaned))
    targets: list[tuple[Any, dict[str, Any], str]] = []
    for (_, digest), rows in grouped.items():
        target, cleaned = rows[0]
        targets.append((target, cleaned, digest))
        for duplicate, _ in rows[1:]:
            bind.execute(
                _job_runs.update()
                .where(_job_runs.c.configuration_snapshot_id == duplicate["id"])
                .values(configuration_snapshot_id=target["id"])
            )
            bind.execute(_job_snapshots.delete().where(_job_snapshots.c.id == duplicate["id"]))
    for target, cleaned, digest in targets:
        bind.execute(
            _job_snapshots.update()
            .where(_job_snapshots.c.id == target["id"])
            .values(configuration=cleaned, configuration_hash=digest)
        )


def downgrade() -> None:
    pass
