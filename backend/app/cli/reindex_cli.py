"""Reindex / reprocess CLI for post-tokenizer migrations."""

from __future__ import annotations

import argparse
import asyncio
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.jobs import build_job_service
from app.core.config import get_settings
from app.models.document import DocumentStatus
from app.models.index_build import ProjectIndexPointer
from app.models.project import Project
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.document_service import DocumentService
from app.modules.projects.repositories.project_ai_config_repository import (
    ProjectAIConfigRepository,
)
from app.platform.config.project_ai import ConfigRevisionRecord, resolve_project_ai_config
from app.platform.db.session import Database
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import JobConfiguration
from app.platform.jobs.implementations.job_queue_factory import create_job_queue
from app.platform.providers.implementations.storage_factory import create_storage_provider

logger = structlog.get_logger(__name__)


async def _reprocess_document(project_id: uuid.UUID, document_id: uuid.UUID) -> None:
    settings = get_settings()
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            repository = DocumentRepository(session, project_id)
            queue = create_job_queue(settings)
            jobs = build_job_service(
                session=session,
                project_id=project_id,
                settings=settings,
                queue=queue,
            )
            service = DocumentService(
                session=session,
                repository=repository,
                storage=create_storage_provider(settings),
                job_submitter=jobs,
                job_configuration=await _project_job_configuration(
                    session,
                    project_id=project_id,
                ),
                job_max_attempts=settings.jobs.max_attempts,
                max_upload_bytes=settings.knowledge.max_upload_bytes,
            )
            await service.reprocess(document_id)
        logger.info(
            "reindex_document_enqueued",
            project_id=str(project_id),
            document_id=str(document_id),
        )
    finally:
        await database.dispose()


async def _reprocess_project(
    project_id: uuid.UUID,
    *,
    full: bool,
    dry_run: bool,
) -> int:
    settings = get_settings()
    database = Database(settings)
    count = 0
    try:
        async with database.session_factory() as session:
            repository = DocumentRepository(session, project_id)
            queue = create_job_queue(settings)
            jobs = build_job_service(
                session=session,
                project_id=project_id,
                settings=settings,
                queue=queue,
            )
            service = DocumentService(
                session=session,
                repository=repository,
                storage=create_storage_provider(settings),
                job_submitter=jobs,
                job_configuration=await _project_job_configuration(
                    session,
                    project_id=project_id,
                ),
                job_max_attempts=settings.jobs.max_attempts,
                max_upload_bytes=settings.knowledge.max_upload_bytes,
            )
            documents = await repository.list_page(limit=10_000, offset=0)
            for document in documents:
                if document.status is DocumentStatus.FAILED and not full:
                    continue
                if dry_run:
                    logger.info(
                        "reindex_dry_run",
                        project_id=str(project_id),
                        document_id=str(document.id),
                        status=document.status.value,
                    )
                    count += 1
                    continue

                await service.reprocess(document.id)
                count += 1
    finally:
        await database.dispose()
    return count


async def _project_job_configuration(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
) -> JobConfiguration:
    """Capture the same Project config/index/source provenance as HTTP submissions."""
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, project_id).get_active()
    project = await session.get(Project, project_id)
    pointer = await session.get(ProjectIndexPointer, project_id)
    resolution = resolve_project_ai_config(
        settings,
        ConfigRevisionRecord(
            id=revision.id,
            revision_number=revision.revision_number,
            configuration_hash=revision.configuration_hash,
            configuration=dict(revision.configuration),
        )
        if revision is not None
        else None,
    )
    return build_job_configuration(
        settings,
        resolution=resolution,
        active_index_build_id=(
            str(pointer.active_build_id) if pointer and pointer.active_build_id else None
        ),
        source_metadata_generation=(
            project.source_metadata_generation if project is not None else 0
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reindex documents after tokenizer upgrades.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    document_parser = subparsers.add_parser("document", help="Reprocess a single document")
    document_parser.add_argument("--project-id", required=True)
    document_parser.add_argument("--document-id", required=True)

    project_parser = subparsers.add_parser("project", help="Reprocess documents in a project")
    project_parser.add_argument("--project-id", required=True)
    project_parser.add_argument("--full", action="store_true", help="Include failed documents")
    project_parser.add_argument(
        "--dry-run", action="store_true", help="List targets without enqueue"
    )
    return parser


async def _main_async(args: argparse.Namespace) -> None:
    if args.command == "document":
        await _reprocess_document(
            uuid.UUID(args.project_id),
            uuid.UUID(args.document_id),
        )
        return

    count = await _reprocess_project(
        uuid.UUID(args.project_id),
        full=args.full,
        dry_run=args.dry_run,
    )
    logger.info(
        "reindex_project_complete",
        project_id=args.project_id,
        document_count=count,
        dry_run=args.dry_run,
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
