"""FastAPI dependencies for the Knowledge module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.audit import DatabaseAuditRecorder
from app.core.config import get_settings
from app.dependencies.access import AdminOrOrganizationDep
from app.dependencies.common import DbSessionDep
from app.dependencies.jobs import get_job_submitter
from app.models.index_build import ProjectIndexPointer
from app.models.project import Project
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.repositories.source_metadata_repository import (
    SourceMetadataRepository,
)
from app.modules.knowledge.services.document_service import DocumentService
from app.modules.knowledge.services.file_validation_service import FileValidationService
from app.modules.knowledge.services.source_metadata_service import SourceMetadataService
from app.modules.projects.repositories.project_ai_config_repository import ProjectAIConfigRepository
from app.platform.config.project_ai import ConfigRevisionRecord, resolve_project_ai_config
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.implementations.malware_scanner_provider import create_malware_scanner
from app.platform.providers.implementations.storage_factory import get_storage_provider


def get_document_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> DocumentRepository:
    return DocumentRepository(session, project_id)


def get_storage() -> BaseStorageProvider:
    return get_storage_provider()


def get_source_metadata_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> SourceMetadataRepository:
    return SourceMetadataRepository(session, project_id)


def get_source_metadata_service(
    session: DbSessionDep,
    repository: Annotated[SourceMetadataRepository, Depends(get_source_metadata_repository)],
    auth_org: AdminOrOrganizationDep,
) -> SourceMetadataService:
    actor_id = str(auth_org.api_key_id) if auth_org.api_key_id is not None else "operator"
    return SourceMetadataService(
        session,
        repository,
        audit=DatabaseAuditRecorder(session, repository.project_id),
        actor_id=actor_id,
    )


async def get_document_service(
    session: DbSessionDep,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[BaseStorageProvider, Depends(get_storage)],
    job_submitter: Annotated[DurableJobSubmitter, Depends(get_job_submitter)],
    source_metadata_service: Annotated[SourceMetadataService, Depends(get_source_metadata_service)],
) -> DocumentService:
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, repository.project_id).get_active()
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
    project = await session.get(Project, repository.project_id)
    pointer = await session.get(ProjectIndexPointer, repository.project_id)
    return DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        malware_scanner=create_malware_scanner(settings),
        file_validator=FileValidationService(),
        job_submitter=job_submitter,
        job_configuration=build_job_configuration(
            settings,
            resolution=resolution,
            active_index_build_id=(
                str(pointer.active_build_id) if pointer and pointer.active_build_id else None
            ),
            source_metadata_generation=(
                project.source_metadata_generation if project is not None else 0
            ),
        ),
        job_max_attempts=settings.jobs.max_attempts,
        max_upload_bytes=settings.knowledge.max_upload_bytes,
        source_metadata_service=source_metadata_service,
        audit=DatabaseAuditRecorder(session, repository.project_id),
    )


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
SourceMetadataServiceDep = Annotated[SourceMetadataService, Depends(get_source_metadata_service)]
