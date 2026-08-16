"""Phase 1 policy, provenance, audit, and ownership foundations.

Revision ID: 0023_phase1_policy
Revises: 0022_super_admin_auth
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings
from app.platform.config.project_ai import resolve_project_ai_config, stable_hash
from app.platform.domain.auth_context import DEFAULT_ORGANIZATION_ID

revision: str = "0023_phase1_policy"
down_revision: str | None = "0022_super_admin_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "ownership_locked", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "source_metadata_generation",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE projects SET ownership_locked = false WHERE organization_id = :org_id"
        ).bindparams(org_id=DEFAULT_ORGANIZATION_ID)
    )

    op.create_table(
        "project_ai_config_revisions",
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("restored_from_revision_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_ai_config_revisions")),
        sa.UniqueConstraint(
            "project_id", "revision_number", name="uq_project_ai_config_revision_number"
        ),
    )
    op.create_index(
        "ix_project_ai_config_revisions_project_created",
        "project_ai_config_revisions",
        ["project_id", "created_at"],
    )
    op.add_column("projects", sa.Column("active_ai_config_revision_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_projects_active_ai_config_revision_id",
        "projects",
        "project_ai_config_revisions",
        ["active_ai_config_revision_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )

    op.create_table(
        "conversation_config_snapshots",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("origins", postgresql.JSONB(), nullable=False),
        sa.Column("compatibility_diagnostics", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_config_snapshots")),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_config_snapshot_sequence"
        ),
    )
    op.create_index(
        "ix_conversation_config_snapshots_project_conversation",
        "conversation_config_snapshots",
        ["project_id", "conversation_id", "created_at"],
    )
    op.add_column(
        "conversations", sa.Column("active_config_snapshot_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_conversations_active_config_snapshot_id",
        "conversations",
        "conversation_config_snapshots",
        ["active_config_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )
    _backfill_conversation_snapshots()

    op.add_column("messages", sa.Column("config_snapshot_id", sa.Uuid(), nullable=True))
    op.add_column(
        "messages",
        sa.Column(
            "config_provenance",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_messages_config_snapshot_id",
        "messages",
        "conversation_config_snapshots",
        ["config_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_messages_config_snapshot_id", "messages", ["config_snapshot_id"])
    op.execute(
        """
        UPDATE messages m
        SET config_snapshot_id = c.active_config_snapshot_id,
            config_provenance = s.provenance
        FROM conversations c
        JOIN conversation_config_snapshots s ON s.id = c.active_config_snapshot_id
        WHERE m.conversation_id = c.id
        """
    )

    for table in ("generations", "evaluation_runs"):
        op.add_column(
            table,
            sa.Column(
                "config_snapshot",
                postgresql.JSONB(),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "config_provenance",
                postgresql.JSONB(),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )
    op.add_column("generations", sa.Column("configuration_hash", sa.String(64), nullable=True))

    op.add_column("audit_events", sa.Column("organization_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE audit_events a
        SET organization_id = p.organization_id
        FROM projects p
        WHERE a.project_id = p.id
        """
    )
    op.drop_constraint(op.f("fk_audit_events_project_id_projects"), "audit_events", type_="foreignkey")
    op.alter_column("audit_events", "project_id", existing_type=sa.Uuid(), nullable=True)
    op.create_foreign_key(
        "fk_audit_events_project_id_projects",
        "audit_events",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_audit_events_organization_id_organizations",
        "audit_events",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_audit_events_organization_created",
        "audit_events",
        ["organization_id", "created_at", "id"],
    )


def _backfill_conversation_snapshots() -> None:
    connection = op.get_bind()
    settings = get_settings()
    base = resolve_project_ai_config(settings, None)
    rows = connection.execute(
        sa.text(
            """
            SELECT id, project_id, provider, model, temperature, system_prompt_version
            FROM conversations
            ORDER BY project_id, id
            """
        )
    ).mappings()
    for row in rows:
        configuration = base.configuration.model_dump(mode="json")
        origins = dict(base.origins)
        for column, path in (
            ("provider", "llm.provider"),
            ("model", "llm.model"),
            ("temperature", "llm.temperature"),
            ("system_prompt_version", "prompt_version"),
        ):
            value = row[column]
            if value is None:
                continue
            if "." in path:
                section, field = path.split(".", 1)
                configuration[section][field] = value
            else:
                configuration[path] = value
            origins[path] = "legacy_conversation_backfill"
        snapshot_id = uuid.uuid4()
        config_hash = stable_hash(configuration)
        provenance = base.provenance.model_dump(mode="json")
        provenance["prompt_versions"] = {
            "chat": configuration["prompt_version"],
            "profile": configuration["prompt_profile"],
        }
        connection.execute(
            sa.text(
                """
                INSERT INTO conversation_config_snapshots (
                    id, project_id, conversation_id, sequence, configuration_hash,
                    configuration, provenance, origins, compatibility_diagnostics,
                    created_by, reason
                ) VALUES (
                    :id, :project_id, :conversation_id, 1, :configuration_hash,
                    CAST(:configuration AS jsonb), CAST(:provenance AS jsonb),
                    CAST(:origins AS jsonb), CAST(:diagnostics AS jsonb),
                    'migration', 'Phase 1 legacy conversation backfill'
                )
                """
            ),
            {
                "id": snapshot_id,
                "project_id": row["project_id"],
                "conversation_id": row["id"],
                "configuration_hash": config_hash,
                "configuration": json.dumps(configuration, sort_keys=True),
                "provenance": json.dumps(provenance, sort_keys=True),
                "origins": json.dumps(origins, sort_keys=True),
                "diagnostics": json.dumps(
                    [
                        key
                        for key in (
                            "provider",
                            "model",
                            "temperature",
                            "system_prompt_version",
                        )
                        if row[key] is not None
                    ]
                ),
            },
        )
        connection.execute(
            sa.text(
                "UPDATE conversations SET active_config_snapshot_id = :snapshot_id WHERE id = :id"
            ),
            {"snapshot_id": snapshot_id, "id": row["id"]},
        )


def downgrade() -> None:
    raise RuntimeError("Phase 1 policy/provenance migration is intentionally irreversible")
