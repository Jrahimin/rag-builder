"""Unit tests for ResultHydrator."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.retrievers.result_hydrator import ResultHydrator

pytestmark = pytest.mark.unit


def _chunk(project_id: uuid.UUID, document_id: uuid.UUID) -> DocumentChunk:
    return DocumentChunk(
        id=uuid.uuid4(),
        project_id=project_id,
        document_id=document_id,
        chunk_index=0,
        content="chunk content",
        page_number=1,
        char_start=0,
        char_end=13,
        token_count=2,
        chunk_metadata={"source": "handbook"},
    )


def _document(project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    return Document(
        id=document_id,
        project_id=project_id,
        filename="doc.txt",
        content_type="text/plain",
        size_bytes=10,
        storage_key="k",
        content_sha256="abc",
        status=DocumentStatus.READY,
        version=1,
    )


async def test_hydrator_preserves_candidate_order_and_drops_orphans() -> None:
    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    present = _chunk(project_id, document_id)
    missing = uuid.uuid4()
    hydrator = ResultHydrator(AsyncMock(), project_id)
    chunk_repository = MagicMock()
    chunk_repository.map_by_ids = AsyncMock(return_value={present.id: present})
    document_repository = MagicMock()
    document_repository.map_by_ids = AsyncMock(
        return_value={document_id: _document(project_id, document_id)}
    )
    hydrator._chunk_repository = chunk_repository
    hydrator._document_repository = document_repository

    results = await hydrator.hydrate(
        [
            CandidateHit(missing, 0.5, CandidateSource.HYBRID),
            CandidateHit(
                present.id,
                0.9,
                CandidateSource.HYBRID,
                semantic_score=0.72,
            ),
        ]
    )

    assert len(results) == 1
    assert results[0].chunk_id == present.id
    assert results[0].filename == "doc.txt"
    assert results[0].semantic_score == 0.72


async def test_hydrator_strips_translated_query_from_result_metadata() -> None:
    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    present = _chunk(project_id, document_id)
    hydrator = ResultHydrator(AsyncMock(), project_id)
    hydrator._chunk_repository = MagicMock()
    hydrator._chunk_repository.map_by_ids = AsyncMock(return_value={present.id: present})
    hydrator._document_repository = MagicMock()
    hydrator._document_repository.map_by_ids = AsyncMock(
        return_value={document_id: _document(project_id, document_id)}
    )

    results = await hydrator.hydrate(
        [
            CandidateHit(
                present.id,
                0.03,
                CandidateSource.HYBRID,
                metadata={
                    "translated_query": "উৎসে কর সংগ্রহের খাত",
                    "translation_status": "applied",
                    "rrf_contributions": [{"branch_id": "translated_dense:bn", "rank": 1}],
                },
            )
        ]
    )

    assert "translated_query" not in results[0].metadata
    assert results[0].metadata["translation_status"] == "applied"
    assert results[0].metadata["rrf_contributions"][0]["branch_id"] == "translated_dense:bn"

