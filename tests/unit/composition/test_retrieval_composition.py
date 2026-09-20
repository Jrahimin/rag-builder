"""Characterization tests for retrieval dependency composition."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.composition.retrieval import build_indexing_service
from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.core.config import EmbeddingConfig, JobQueueBackend, JobsConfig, RetrievalConfig, Settings
from app.platform.config.project_ai import SourcePolicyMode
from app.platform.jobs.contracts import JobQueue

pytestmark = pytest.mark.unit


def test_build_indexing_service_uses_one_settings_snapshot_and_explicit_overrides() -> None:
    queue = AsyncMock(spec=JobQueue)
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
    )

    assert service._job_submitter._queue is queue
    assert service.embedding_set_version == 3


async def test_scoped_document_capture_still_enforces_source_applicability() -> None:
    adapter = KnowledgeRetrievalSourceMetadataAdapter(AsyncMock())
    adapter._reader.capture = AsyncMock(
        return_value=SimpleNamespace(
            selectable=object(),
            generation=7,
            reference_date=datetime(2026, 9, 20, tzinfo=UTC).date(),
            explicit_as_of=None,
            exclusion_counts={"draft": 1},
        )
    )

    result = await adapter.capture(
        project_id=uuid.uuid4(),
        configured_mode=SourcePolicyMode.ENFORCE,
        deployment_cap="enforce",
        as_of=None,
        scoped_document_id=uuid.uuid4(),
    )

    assert result.effective_mode is SourcePolicyMode.ENFORCE
    assert adapter._reader.capture.await_args.kwargs["enforce"] is True
