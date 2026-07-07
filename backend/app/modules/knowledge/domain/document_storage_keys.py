"""Document parsed sidecar storage key helpers."""

from __future__ import annotations

import uuid

from app.models.document import Document


def build_document_storage_prefix(project_id: uuid.UUID, document_id: uuid.UUID) -> str:
    """Directory prefix for all artifacts owned by one document."""
    return f"{project_id}/{document_id}"


def build_parsed_text_storage_key(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    version: int,
) -> str:
    return f"{project_id}/{document_id}/parsed/v{version}.txt"


def build_parsed_json_storage_key(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    version: int,
) -> str:
    return f"{project_id}/{document_id}/parsed/v{version}.json"


def iter_parsed_artifact_keys(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    version: int,
) -> tuple[str, ...]:
    """All parsed text/json sidecar keys for versions 1..version (inclusive)."""
    keys: list[str] = []
    for artifact_version in range(1, max(version, 1) + 1):
        keys.append(build_parsed_text_storage_key(project_id, document_id, artifact_version))
        keys.append(build_parsed_json_storage_key(project_id, document_id, artifact_version))
    return tuple(keys)


def iter_document_storage_keys(document: Document) -> tuple[str, ...]:
    """Raw upload plus every parsed sidecar key for a document."""
    return (
        document.storage_key,
        *iter_parsed_artifact_keys(document.project_id, document.id, document.version),
    )
