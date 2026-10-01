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
