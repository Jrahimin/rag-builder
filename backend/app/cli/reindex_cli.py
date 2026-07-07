"""Reindex / reprocess CLI for post-tokenizer migrations."""

from __future__ import annotations

import argparse
import asyncio
import uuid

import structlog

from app.core.config import get_settings
from app.models.document import DocumentStatus
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.document_service import DocumentService
from app.platform.db.session import Database
from app.platform.jobs.implementations.job_queue_factory import get_job_queue
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.worker.handlers.document import run_document_process

logger = structlog.get_logger(__name__)


async def _noop_ensure_project() -> None:
    return None


async def _reprocess_document(project_id: uuid.UUID, document_id: uuid.UUID) -> None:
    await run_document_process(project_id=project_id, document_id=document_id)
    logger.info("reindex_document_complete", project_id=str(project_id), document_id=str(document_id))


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

                service = DocumentService(
                    session=session,
                    repository=repository,
                    storage=create_storage_provider(settings),
                    job_queue=get_job_queue(),
                    ensure_project=_noop_ensure_project,
                    max_upload_bytes=settings.knowledge.max_upload_bytes,
                )
                await service.reprocess(document.id)
                count += 1
    finally:
        await database.dispose()
    return count


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reindex documents after tokenizer upgrades.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    document_parser = subparsers.add_parser("document", help="Reprocess a single document")
    document_parser.add_argument("--project-id", required=True)
    document_parser.add_argument("--document-id", required=True)

    project_parser = subparsers.add_parser("project", help="Reprocess documents in a project")
    project_parser.add_argument("--project-id", required=True)
    project_parser.add_argument("--full", action="store_true", help="Include failed documents")
    project_parser.add_argument("--dry-run", action="store_true", help="List targets without enqueue")
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
