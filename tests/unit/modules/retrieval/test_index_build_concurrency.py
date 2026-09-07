"""Corpus mutation invalidates a build, not the source document."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.retrieval.workflows.index_build_workflow import IndexBuildWorkflow
from app.platform.jobs.errors import JobError
from app.platform.jobs.failure import classify_job_failure

pytestmark = pytest.mark.unit


async def test_changed_document_version_rejects_snapshot_with_retryable_failure():
    session = AsyncMock()
    session.scalar.return_value = 2
    workflow = IndexBuildWorkflow(
        session,
        uuid.uuid4(),
        MagicMock(),
        embedding_set_version=3,
        batch_size=32,
        filterable_metadata_keys=[],
        fts_regconfig="simple",
    )
    with pytest.raises(JobError) as error:
        await workflow._validate_versions(
            [{"document_id": str(uuid.uuid4()), "document_version": 1}]
        )
    failure = classify_job_failure(error.value)
    assert failure.code == "index_build_corpus_changed"
    assert failure.retryable is True
    assert failure.details["expected"] == 1
    assert failure.details["actual"] == 2
