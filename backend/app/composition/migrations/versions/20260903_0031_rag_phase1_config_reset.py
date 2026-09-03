"""one-shot RAG Phase 1 configuration reset

Revision ID: 0031_rag_phase1_reset
Revises: 0030_config_phase2
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
    legacy_project_configuration_to_v2,
    reset_job_snapshot_configuration,
    reset_snapshot_configuration,
)
from app.platform.config.project_ai import ProjectAIConfig, stable_hash
from app.platform.jobs.contracts import JobConfiguration

revision: str = "0031_rag_phase1_reset"
down_revision: str | None = "0030_config_phase2"
branch_labels: str | None = None
depends_on: str | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_projects = sa.table(
    "projects",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("active_ai_config_revision_id", postgresql.UUID(as_uuid=True)),
)
_revisions = sa.table(
    "project_ai_config_revisions",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("project_id", postgresql.UUID(as_uuid=True)),
    sa.column("revision_number", sa.Integer()),
    sa.column("schema_version", sa.Integer()),
    sa.column("configuration_hash", sa.String()),
    sa.column("configuration", _JSONB),
    sa.column("created_by", sa.String()),
    sa.column("source", sa.String()),
    sa.column("reason", sa.Text()),
    sa.column("restored_from_revision_id", postgresql.UUID(as_uuid=True)),
)
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

    active_v1 = bind.execute(
        sa.select(
            _projects.c.id.label("project_id"),
            _revisions.c.id.label("revision_id"),
            _revisions.c.configuration,
        )
        .select_from(
            _projects.join(
                _revisions,
                _projects.c.active_ai_config_revision_id == _revisions.c.id,
            )
        )
        .where(_revisions.c.schema_version == 1)
        .with_for_update()
    ).mappings()
    for row in active_v1:
        project_id = row["project_id"]
        legacy_id = row["revision_id"]
        canonical = legacy_project_configuration_to_v2(
            settings,
            dict(row["configuration"]),
        )
        payload = canonical.model_dump(mode="json", exclude_none=True)
        next_number = int(
            bind.scalar(
                sa.select(sa.func.coalesce(sa.func.max(_revisions.c.revision_number), 0)).where(
                    _revisions.c.project_id == project_id
                )
            )
            or 0
        ) + 1
        new_id = uuid.uuid4()
        bind.execute(
            _revisions.insert().values(
                id=new_id,
                project_id=project_id,
                revision_number=next_number,
                schema_version=2,
                configuration_hash=stable_hash(payload),
                configuration=payload,
                created_by="config-reset",
                source="pre_public_v2_reset",
                reason="One-shot pre-public V1 to V2 configuration reset",
                restored_from_revision_id=legacy_id,
            )
        )
        bind.execute(
            _projects.update()
            .where(_projects.c.id == project_id)
            .values(active_ai_config_revision_id=new_id)
        )

    for row in bind.execute(
        sa.select(
            _revisions.c.id,
            _revisions.c.configuration,
            _revisions.c.configuration_hash,
        ).where(_revisions.c.schema_version == 2)
    ).mappings():
        cleaned = reset_snapshot_configuration(dict(row["configuration"]))
        ProjectAIConfig.model_validate(cleaned)
        digest = stable_hash(cleaned)
        if cleaned != row["configuration"] or digest != row["configuration_hash"]:
            bind.execute(
                _revisions.update()
                .where(_revisions.c.id == row["id"])
                .values(configuration=cleaned, configuration_hash=digest)
            )

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
    # The reset intentionally drops ambiguous compatibility state. Alembic
    # history remains intact, but the data rewrite is not reversible.
    pass
