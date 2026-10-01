"""Bounded operator diagnostics: no arbitrary provider/configuration payloads."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

from app.core.exceptions import NotFoundError
from app.models.message_diagnostic import MessageDiagnostic
from app.modules.conversations.repositories.message_diagnostic_repository import (
    MessageDiagnosticRepository,
)
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder

MAX_PAYLOAD_BYTES = 262144
DIAGNOSTIC_TTL = timedelta(days=7)


@cache
def execution_fingerprint() -> dict[str, str]:
    from app.modules.conversations.answer_draft import AnswerDraft
    from app.modules.conversations.services.message_execution_runner_service import (
        LOADED_RUNNER_SHA256,
    )

    return {
        "loaded_runner_sha256": LOADED_RUNNER_SHA256,
        "answer_draft_schema_sha256": hashlib.sha256(
            json.dumps(AnswerDraft.model_json_schema(), sort_keys=True).encode()
        ).hexdigest(),
    }


def bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Contract-derived allowlist excludes credentials, raw provider responses and hidden reasoning.
    result = {
        key: payload[key]
        for key in (
            "intent",
            "requirements",
            "evidence",
            "verified_assertions",
            "limitations",
            "rejected_attempts",
            "terminal",
            "fatal_event",
            "recovery_stop",
            "correction_attempts",
        )
        if key in payload
    }
    encoded = json.dumps(result, ensure_ascii=False, default=str).encode()
    if len(encoded) > MAX_PAYLOAD_BYTES:
        return {
            "terminal": result.get("terminal"),
            "truncated": True,
            "payload_hash": hashlib.sha256(encoded).hexdigest(),
            "original_bytes": len(encoded),
        }
    return result


def diagnostic_record(
    *,
    project_id: uuid.UUID,
    message_id: uuid.UUID,
    metadata: dict[str, Any],
    full_capture: bool,
    payload: dict[str, Any] | None,
    now: datetime | None = None,
) -> MessageDiagnostic:
    now = now or datetime.now(UTC)
    summary = {
        "version": "message.diagnostic.v1",
        "terminal": metadata.get("terminal_outcome"),
        "execution": metadata.get("execution"),
        "attempted_claim_counts": metadata.get("attempted_claim_verification_counts"),
        "published_claim_counts": metadata.get("published_claim_verification_counts"),
        "prompt_version": metadata.get("prompt_version"),
        "provider_route": metadata.get("provider_route"),
        "stage_provider_routes": [
            {
                key: call[key]
                for key in ("kind", "provider", "model", "purpose", "work_purpose")
                if key in call
            }
            for call in (metadata.get("lifecycle") or {}).get("provider_calls", [])[:64]
        ],
        "code_fingerprint": execution_fingerprint(),
        "index_build_id": metadata.get("index_build_id"),
        "source_metadata_generation": metadata.get("source_metadata_generation"),
        "configuration_hash": metadata.get("retrieval_configuration_hash"),
    }
    encoded_summary = json.dumps(summary, default=str).encode()
    if len(encoded_summary) > 16384:
        summary = {
            "version": "message.diagnostic.v1",
            "terminal": metadata.get("terminal_outcome"),
            "truncated": True,
            "original_bytes": len(encoded_summary),
            "code_fingerprint": execution_fingerprint(),
        }
    return MessageDiagnostic(
        project_id=project_id,
        message_id=message_id,
        summary=summary,
        payload=bounded_payload(payload) if full_capture and payload else None,
        expires_at=now + DIAGNOSTIC_TTL if full_capture and payload else None,
    )


class MessageDiagnosticService:
    def __init__(
        self, repository: MessageDiagnosticRepository, audit: AuditRecorder, actor_id: str
    ):
        self.repository = repository
        self.audit = audit
        self.actor_id = actor_id

    async def get(self, message_id: uuid.UUID) -> dict[str, Any]:
        record = await self.repository.get(message_id)
        self.audit.record(
            event_type=AuditEventType.MESSAGE_DIAGNOSTIC_READ,
            actor_type=AuditActorType.OPERATOR,
            actor_id=self.actor_id,
            resource_type="message_diagnostic",
            resource_id=message_id,
            outcome=AuditOutcome.SUCCESS if record is not None else AuditOutcome.FAILURE,
        )
        await self.repository.session.commit()
        if record is None:
            raise NotFoundError(
                message="Message diagnostic not found.", code="message_diagnostic_not_found"
            )
        return {
            "message_id": message_id,
            "project_id": self.repository.project_id,
            "summary": record.summary,
            "payload": record.payload,
            "expires_at": record.expires_at,
            "expired": record.expires_at is not None and record.expires_at <= datetime.now(UTC),
        }
