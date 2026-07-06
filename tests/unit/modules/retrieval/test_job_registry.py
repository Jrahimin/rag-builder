"""Unit tests for job registry."""

from __future__ import annotations

import pytest

from app.platform.jobs.names import DOCUMENT_EMBED, DOCUMENT_INDEX, DOCUMENT_PROCESS
from app.platform.jobs.registry import get_job_registry


@pytest.mark.unit
def test_job_registry_includes_all_document_jobs() -> None:
    registry = get_job_registry()
    assert DOCUMENT_PROCESS in registry
    assert DOCUMENT_EMBED in registry
    assert DOCUMENT_INDEX in registry
