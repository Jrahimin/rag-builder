"""Operator-only capture, bounded diagnostic storage and public-output sanitation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ForbiddenError
from app.dependencies.conversations import diagnostic_capture_authorization
from app.models.message import Message, MessageRole
from app.modules.conversations.repositories.message_diagnostic_repository import (
    MessageDiagnosticRepository,
)
from app.modules.conversations.schemas.message import MessageResponse
from app.modules.conversations.services.message_diagnostic_service import (
    MessageDiagnosticService,
    bounded_payload,
    diagnostic_record,
)
from app.platform.audit.contracts import AuditEventType

pytestmark = pytest.mark.unit


async def test_full_capture_requires_operator_and_rejects_unknown_mode(monkeypatch):
    monkeypatch.setattr(
        "app.dependencies.conversations.get_settings",
        lambda: SimpleNamespace(auth=SimpleNamespace(enabled=True)),
    )
    with pytest.raises(ForbiddenError):
        await diagnostic_capture_authorization(SimpleNamespace(is_platform_admin=False), "full")
    with pytest.raises(ForbiddenError):
        await diagnostic_capture_authorization(SimpleNamespace(is_platform_admin=True), "all")
    assert await diagnostic_capture_authorization(SimpleNamespace(is_platform_admin=True), "full")
    assert not await diagnostic_capture_authorization(
        SimpleNamespace(is_platform_admin=False), None
    )


def test_full_payload_is_opt_in_bounded_and_expires_after_seven_days():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    args = {
        "project_id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "metadata": {},
        "now": now,
        "payload": {
            "rejected_attempts": [{"id": "A1", "text": "rejected claim"}],
            "api_key": "secret",
            "reasoning": "hidden",
        },
    }
    normal = diagnostic_record(**args, full_capture=False)
    full = diagnostic_record(**args, full_capture=True)
    assert normal.payload is None and normal.expires_at is None
    assert full.expires_at == now + timedelta(days=7)
    assert set(full.payload) == {"rejected_attempts"}
    assert bounded_payload({"evidence": [{"text": "x" * 300000}]})["truncated"]


async def test_diagnostic_read_is_project_scoped_expires_payload_and_audits():
    project = uuid.uuid4()
    message = uuid.uuid4()
    session = AsyncMock()
    audit = MagicMock()
    record = SimpleNamespace(
        summary={"version": "v1"}, payload=None, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    session.scalar.return_value = record
    result = await MessageDiagnosticService(
        MessageDiagnosticRepository(session, project), audit, "admin"
    ).get(message)
    stmt = session.scalar.call_args.args[0]
    params = stmt.compile().params
    assert project in params.values() and message in params.values()
    expiry = session.execute.call_args.args[0].compile().params
    assert project in expiry.values()
    assert result["expired"] and result["payload"] is None
    assert audit.record.call_args.kwargs["event_type"] == AuditEventType.MESSAGE_DIAGNOSTIC_READ
    session.commit.assert_awaited_once()


def test_public_response_never_exposes_operator_assertions():
    now = datetime.now(UTC)
    message = Message(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        role=MessageRole.ASSISTANT,
        content="Verified public answer",
        created_at=now,
        updated_at=now,
        message_metadata={
            "operator_diagnostic": {"rejected_attempts": [{"text": "private rejected assertion"}]},
            "answer_draft": {
                "status": "rendered",
                "segments": [{"text": "private rejected assertion"}],
            },
        },
        config_provenance={},
        citations=[],
        claims=[],
    )
    public = MessageResponse.from_message(message).model_dump_json()
    assert "operator_diagnostic" not in public
    assert "private rejected assertion" not in public


def test_phase2_graph_and_rejected_dimensions_remain_protected():
    payload = {
        "calculations": {
            "status": "verified",
            "graph": {
                "nodes": [
                    {
                        "id": "N1",
                        "operation": "minimum",
                        "operands": ["input:investment", "source:cap"],
                        "result": "100",
                    }
                ]
            },
        },
        "rejected_attempts": [
            {
                "id": "A1",
                "text": "Rejected private employee assertion",
                "deterministic_checks": {
                    "verifier_failures": [
                        {
                            "assertion_id": "A1",
                            "dimension": "subject_category",
                            "evidence_binding": ["bound-source"],
                        }
                    ]
                },
            }
        ],
        "raw_provider": "must not leak",
    }
    normal = diagnostic_record(
        project_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        metadata={},
        full_capture=False,
        payload=payload,
    )
    captured = diagnostic_record(
        project_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        metadata={},
        full_capture=True,
        payload=payload,
    )
    assert normal.payload is None
    assert captured.payload["calculations"] == payload["calculations"]
    assert captured.payload["rejected_attempts"] == payload["rejected_attempts"]
    assert "raw_provider" not in captured.payload


async def test_background_sweep_expires_inactive_evaluation_projects_with_scoped_updates():
    from app.composition.message_diagnostic_retention import expire_message_diagnostics

    expired_project, retained_project = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    records = [
        SimpleNamespace(
            project_id=expired_project,
            expires_at=now - timedelta(days=1),
            payload={"terminal": "expired"},
        ),
        SimpleNamespace(
            project_id=expired_project,
            expires_at=now + timedelta(days=1),
            payload={"terminal": "current"},
        ),
        SimpleNamespace(
            project_id=retained_project,
            expires_at=now + timedelta(days=1),
            payload={"terminal": "other"},
        ),
    ]
    updates = []

    async def select_projects(statement):
        sql = str(statement)
        assert "DISTINCT" in sql and "payload IS NOT NULL" in sql and "expires_at <=" in sql
        if "message_diagnostics" in sql:
            return []  # No Message traffic or Message diagnostics for these Projects.
        cutoff = statement.compile().params["expires_at_1"]
        return list(
            {r.project_id for r in records if r.payload is not None and r.expires_at <= cutoff}
        )

    async def clear_project(statement):
        sql, params = str(statement), statement.compile().params
        assert "evaluation_diagnostics.project_id =" in sql
        assert "expires_at <=" in sql and "payload IS NOT NULL" in sql
        assert params["payload"] is None
        updates.append(params["project_id_1"])
        for row in records:
            if (
                row.project_id == params["project_id_1"]
                and row.expires_at <= params["expires_at_1"]
            ):
                row.payload = None

    session = MagicMock()
    session.scalars = AsyncMock(side_effect=select_projects)
    session.execute = AsyncMock(side_effect=clear_project)
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    await expire_message_diagnostics(lambda: session)
    assert records[0].payload is None
    assert records[1].payload == {"terminal": "current"}
    assert records[2].payload == {"terminal": "other"}
    assert updates == [expired_project]
    session.commit.assert_awaited_once()
