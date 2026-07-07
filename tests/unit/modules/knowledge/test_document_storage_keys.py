"""Unit tests for document storage key helpers."""

from __future__ import annotations

import uuid

import pytest

from app.modules.knowledge.domain.document_storage_keys import (
    build_parsed_json_storage_key,
    build_parsed_text_storage_key,
    iter_document_storage_keys,
    iter_parsed_artifact_keys,
)

pytestmark = pytest.mark.unit


def test_iter_parsed_artifact_keys_includes_all_versions() -> None:
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    document_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

    keys = iter_parsed_artifact_keys(project_id, document_id, version=2)

    assert keys == (
        build_parsed_text_storage_key(project_id, document_id, 1),
        build_parsed_json_storage_key(project_id, document_id, 1),
        build_parsed_text_storage_key(project_id, document_id, 2),
        build_parsed_json_storage_key(project_id, document_id, 2),
    )


def test_iter_document_storage_keys_includes_raw_and_parsed() -> None:
    from datetime import UTC, datetime

    from app.models.document import Document, DocumentStatus

    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        project_id=project_id,
        filename="sample.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_key=f"{project_id}/{document_id}/sample.txt",
        content_sha256="abc",
        status=DocumentStatus.CHUNKED,
        version=1,
        deleted_at=None,
        deleted_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    keys = iter_document_storage_keys(document)

    assert keys[0] == document.storage_key
    assert build_parsed_json_storage_key(project_id, document_id, 1) in keys
