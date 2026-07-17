"""Characterization tests for retrieval dependency composition."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.composition.retrieval import build_indexing_service
from app.core.config import EmbeddingConfig, JobQueueBackend, JobsConfig, RetrievalConfig, Settings
from app.platform.jobs.contracts import JobQueue
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider

pytestmark = pytest.mark.unit


def test_build_indexing_service_uses_one_settings_snapshot_and_explicit_overrides() -> None:
    queue = AsyncMock(spec=JobQueue)
    embedder = HashEmbeddingProvider(model="phase-zero", dimensions=12, provider_version="1")
    settings = Settings(
        embedding=EmbeddingConfig(batch_size=7, dimensions=12, model="phase-zero"),
        retrieval=RetrievalConfig(
            embedding_set_version=3,
            filterable_metadata_keys=["source", "tag"],
        ),
        jobs=JobsConfig(backend=JobQueueBackend.INLINE),
    )

    service = build_indexing_service(
        session=AsyncMock(),
        project_id=uuid.uuid4(),
        settings=settings,
        job_queue=queue,
        embedder=embedder,
    )

    assert service._job_submitter._queue is queue
    assert service._embedder is embedder
    assert service._embedding_batch_size == 7
    assert service._filterable_metadata_keys == ["source", "tag"]
    assert service.embedding_set_version == 3
