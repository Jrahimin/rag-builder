"""Unit tests for transactional retrieval artifact cleanup."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.retrieval.services.retrieval_cleanup_service import (
    RetrievalCleanupService,
)

pytestmark = pytest.mark.unit


async def test_document_cleanup_deletes_native_indexes() -> None:
    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    session = AsyncMock()
    service = RetrievalCleanupService(session, project_id)
    service._embedding_repository = AsyncMock()
    service._keyword_repository = AsyncMock()
    await service.on_document_delete(document_id)

    service._embedding_repository.delete_by_document.assert_awaited_once_with(document_id)
    service._keyword_repository.delete_by_document_all_versions.assert_awaited_once_with(
        document_id
    )
