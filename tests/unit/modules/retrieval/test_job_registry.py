"""Unit tests for job registry."""

from __future__ import annotations

import pytest

from app.platform.jobs.names import (
    CORPUS_REEMBED,
    CORPUS_REINDEX,
    DOCUMENT_DELETE,
    DOCUMENT_EMBED,
    DOCUMENT_INDEX,
    DOCUMENT_PROCESS,
    DOCUMENT_PURGE,
    EVALUATION_RUN,
    STORAGE_RECONCILE,
)
from app.platform.jobs.registry import get_job_registry


@pytest.mark.unit
def test_job_registry_includes_all_document_jobs() -> None:
    registry = get_job_registry()
    assert DOCUMENT_PROCESS in registry
    assert DOCUMENT_EMBED in registry
    assert DOCUMENT_INDEX in registry
    assert EVALUATION_RUN in registry
    assert CORPUS_REEMBED in registry
    assert CORPUS_REINDEX in registry
    assert DOCUMENT_DELETE in registry
    assert DOCUMENT_PURGE in registry
    assert STORAGE_RECONCILE in registry
