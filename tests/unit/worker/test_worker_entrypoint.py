"""Ensure the worker CLI delegates handler registration to code."""

from __future__ import annotations

import runpy

import pytest

pytestmark = pytest.mark.unit


def test_worker_entrypoint_uses_code_owned_taskiq_target() -> None:
    values = runpy.run_path("backend/worker.py", run_name="worker_entrypoint_test")

    assert "_HANDLER_MODULES" not in values
