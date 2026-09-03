"""One-shot pre-public configuration reset command."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.conversation_config_snapshot import ConversationConfigSnapshot
from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.models.job_run import JobRun
from app.models.project import Project
from app.models.project_ai_config_revision import ProjectAIConfigRevision
from app.platform.config.legacy_reset import (
    conversation_invariants,
    legacy_project_configuration_to_v2,
    reset_job_snapshot_configuration,
    reset_snapshot_configuration,
)
from app.platform.config.project_ai import ProjectAIConfig, stable_hash
from app.platform.db.session import Database
from app.platform.jobs.contracts import JobConfiguration


async def reset_legacy_configuration(
    session: AsyncSession,
    settings: Settings,
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Normalize active V1 revisions and rewrite all persisted retired keys."""
    counts: defaultdict[str, int] = defaultdict(int)
    active_rows = (
        await session.execute(
            select(Project, ProjectAIConfigRevision)
            .join(
                ProjectAIConfigRevision,
                Project.active_ai_config_revision_id == ProjectAIConfigRevision.id,
            )
            .where(ProjectAIConfigRevision.schema_version == 1)
            .with_for_update()
        )
    ).all()
    for project, legacy in active_rows:
        configuration = legacy_project_configuration_to_v2(
            settings,
            dict(legacy.configuration),
        )
        payload = configuration.model_dump(mode="json", exclude_none=True)
        next_number = int(
            await session.scalar(
                select(ProjectAIConfigRevision.revision_number)
                .where(ProjectAIConfigRevision.project_id == project.id)
                .order_by(ProjectAIConfigRevision.revision_number.desc())
                .limit(1)
            )
            or 0
        ) + 1
        revision = ProjectAIConfigRevision(
            id=uuid.uuid4(),
            project_id=project.id,
            revision_number=next_number,
            schema_version=2,
            configuration_hash=stable_hash(payload),
            configuration=payload,
            created_by="config-reset",
            source="pre_public_v2_reset",
            reason="One-shot pre-public V1 to V2 configuration reset",
            restored_from_revision_id=legacy.id,
        )
        session.add(revision)
        await session.flush()
        project.active_ai_config_revision_id = revision.id
        counts["active_v1_normalized"] += 1

    revisions = list(
        (
            await session.scalars(
                select(ProjectAIConfigRevision).where(
                    ProjectAIConfigRevision.schema_version == 2
                )
            )
        ).all()
    )
    for revision in revisions:
        cleaned = reset_snapshot_configuration(dict(revision.configuration))
        ProjectAIConfig.model_validate(cleaned)
        if cleaned != revision.configuration:
            revision.configuration = cleaned
            revision.configuration_hash = stable_hash(cleaned)
            counts["v2_revisions_rewritten"] += 1

    conversations = list((await session.scalars(select(ConversationConfigSnapshot))).all())
    for conversation_snapshot in conversations:
        cleaned = reset_snapshot_configuration(dict(conversation_snapshot.configuration))
        invariants = conversation_invariants(cleaned)
        if (
            cleaned != conversation_snapshot.configuration
            or invariants != conversation_snapshot.invariants
        ):
            conversation_snapshot.configuration = cleaned
            conversation_snapshot.invariants = invariants
            conversation_snapshot.configuration_hash = stable_hash(cleaned)
            conversation_snapshot.resolution_fingerprint = stable_hash(
                {
                    "schema_version": conversation_snapshot.schema_version,
                    "effective_value_hash": conversation_snapshot.configuration_hash,
                    "origins": conversation_snapshot.origins,
                    "provenance": conversation_snapshot.provenance,
                    "invariants": invariants,
                }
            )
            counts["conversation_snapshots_rewritten"] += 1

    jobs = list((await session.scalars(select(JobConfigurationSnapshot))).all())
    grouped: defaultdict[
        tuple[uuid.UUID, str],
        list[tuple[JobConfigurationSnapshot, dict[str, Any]]],
    ]
    grouped = defaultdict(list)
    for job_snapshot in jobs:
        cleaned = reset_job_snapshot_configuration(settings, dict(job_snapshot.configuration))
        parsed = JobConfiguration.model_validate(cleaned)
        grouped[(job_snapshot.project_id, parsed.output_digest())].append(
            (job_snapshot, cleaned)
        )
    targets: list[tuple[JobConfigurationSnapshot, dict[str, Any], str]] = []
    for (_, digest), rows in grouped.items():
        target, cleaned = rows[0]
        targets.append((target, cleaned, digest))
        for duplicate, _ in rows[1:]:
            await session.execute(
                update(JobRun)
                .where(JobRun.configuration_snapshot_id == duplicate.id)
                .values(configuration_snapshot_id=target.id)
            )
            await session.delete(duplicate)
            counts["job_snapshot_duplicates_merged"] += 1
    await session.flush()
    for target, cleaned, digest in targets:
        if cleaned != target.configuration or digest != target.configuration_hash:
            target.configuration = cleaned
            target.configuration_hash = digest
            counts["job_snapshots_rewritten"] += 1

    await session.flush()
    counts["active_v1_remaining"] = int(
        await session.scalar(
            select(func.count(Project.id))
            .join(
                ProjectAIConfigRevision,
                Project.active_ai_config_revision_id == ProjectAIConfigRevision.id,
            )
            .where(ProjectAIConfigRevision.schema_version == 1)
        )
        or 0
    )
    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return dict(sorted(counts.items()))


async def _run(*, dry_run: bool) -> dict[str, int]:
    settings = get_settings()
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            return await reset_legacy_configuration(session, settings, dry_run=dry_run)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli config")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset-legacy", help="Reset persisted config to V2-only")
    reset.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    report = asyncio.run(_run(dry_run=bool(args.dry_run)))
    sys.stdout.write(f"{json.dumps(report, sort_keys=True)}\n")
    return 0
