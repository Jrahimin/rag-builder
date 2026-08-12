"""Ensure the worker CLI registers every durable Taskiq handler module."""

from __future__ import annotations

import runpy

import pytest

pytestmark = pytest.mark.unit


def test_worker_entrypoint_includes_all_registered_handler_modules() -> None:
    values = runpy.run_path("backend/worker.py", run_name="worker_entrypoint_test")

    assert values["_HANDLER_MODULES"] == (
        "app.worker.handlers.document",
        "app.worker.handlers.embedding",
        "app.worker.handlers.indexing",
        "app.worker.handlers.evaluation",
        "app.worker.handlers.corpus",
        "app.worker.handlers.document_lifecycle",
        "app.worker.handlers.storage_reconciliation",
    )
