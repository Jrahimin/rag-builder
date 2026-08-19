"""Ensure the worker CLI delegates handler registration to code."""

from __future__ import annotations

import runpy

import pytest

pytestmark = pytest.mark.unit


def test_worker_entrypoint_uses_code_owned_taskiq_target() -> None:
    values = runpy.run_path("backend/worker.py", run_name="worker_entrypoint_test")

    assert "_HANDLER_MODULES" not in values


def test_worker_entrypoint_registers_audit_event_foreign_key_targets() -> None:
    import app.worker.entrypoint  # noqa: F401
    from app.models.audit_event import AuditEvent
    from app.platform.db.base import Base

    assert "organizations" in Base.metadata.tables
    assert {fk.column.table.name for fk in AuditEvent.__table__.foreign_keys} <= set(
        Base.metadata.tables
    )
