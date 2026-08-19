"""Activation marks included chunked documents ready for search."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.document import Document, DocumentStatus
from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
from app.modules.retrieval.workflows.index_build_workflow import (
    document_ids_from_build_manifest,
    mark_included_documents_ready,
)

pytestmark = pytest.mark.unit


def test_document_ids_from_build_manifest_ignores_invalid_rows() -> None:
    document_id = uuid.uuid4()
    versions = document_ids_from_build_manifest(
        {
            "documents": [
                {"document_id": str(document_id), "document_version": 2, "chunk_count": 4},
                {"document_id": "not-a-uuid", "document_version": 1},
                "skip",
            ]
        }
    )
    assert versions == {document_id: 2}


async def test_activation_marks_matching_chunked_documents_ready() -> None:
    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    other_id = uuid.uuid4()
    included = Document(
        id=document_id,
        project_id=project_id,
        filename="policy.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key="raw/policy.txt",
        content_sha256="b" * 64,
        status=DocumentStatus.CHUNKED,
        version=1,
    )
    stale = Document(
        id=other_id,
        project_id=project_id,
        filename="stale.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key="raw/stale.txt",
        content_sha256="a" * 64,
        status=DocumentStatus.CHUNKED,
        version=3,
    )
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value = [included, stale]
    session.execute = AsyncMock(return_value=result)
    build = IndexBuild(
        project_id=project_id,
        operation=IndexBuildOperation.REEMBED,
        state=IndexBuildState.VALIDATED,
        embedding_set_version=2,
        configuration_hash="c" * 64,
        manifest={
            "documents": [
                {"document_id": str(document_id), "document_version": 1, "chunk_count": 2},
                {"document_id": str(other_id), "document_version": 2, "chunk_count": 1},
            ]
        },
    )

    await mark_included_documents_ready(session, project_id, build)

    assert included.status is DocumentStatus.READY
    assert included.error_message is None
    assert stale.status is DocumentStatus.CHUNKED
