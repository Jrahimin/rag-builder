"""Pydantic schemas for messages."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.message import MessageRole
from app.modules.conversations.answer_draft import public_draft_diagnostics
from app.platform.domain.message_proof import (
    AnswerClaim as AnswerClaim,
)
from app.platform.domain.message_proof import (
    CitationSnapshot as CitationSnapshot,
)
from app.platform.domain.message_proof import (
    CitationSourceKind as CitationSourceKind,
)
from app.platform.domain.message_proof import (
    ClaimEvidence as ClaimEvidence,
)
from app.platform.domain.message_proof import (
    ClaimVerification as ClaimVerification,
)


class SourceProvenance(StrEnum):
    """Machine-readable evidence origin for one response."""

    KNOWLEDGE = "knowledge"
    WEB = "web"
    KNOWLEDGE_AND_WEB = "knowledge_and_web"
    NONE = "none"


class SourceScope(StrEnum):
    """Caller-selected source boundary for one message turn."""

    PROJECT_DEFAULT = "project_default"
    INDEXED_ONLY = "indexed_only"


class InsufficientEvidenceReason(StrEnum):
    """Stable reasons for a correct no-answer outcome."""

    NO_RETRIEVAL_RESULTS = "no_retrieval_results"
    BELOW_RELEVANCE_THRESHOLD = "below_relevance_threshold"
    # AUTHORITY_CONTEXT_EMPTY removed in Phase 3: authority redaction now
    # happens before admission so a redacted chunk is simply absent from
    # candidates; CONTEXT_SELECTION_EMPTY covers any remaining empty-after-admit case.
    CONTEXT_SELECTION_EMPTY = "context_selection_empty"
    UNRESOLVED_AUTHORITY = "unresolved_authority"
    CLAIM_VERIFICATION_FAILED = "claim_verification_failed"
    REQUEST_DEADLINE_EXCEEDED = "request_deadline_exceeded"
    RECOVERY_DEADLINE_EXCEEDED = "recovery_deadline_exceeded"
    PROVIDER_TIMEOUT = "provider_timeout"


class ClaimVerificationReason(StrEnum):
    """Stable reason codes for claim verification outcomes."""

    MISSING_CITATION = "missing_citation"
    DURATION_MISMATCH = "duration_mismatch"
    DURATION_NOT_IN_EVIDENCE = "duration_not_in_evidence"
    CONTESTED_GENERALIZATION = "contested_generalization"
    UNPARSED_CALCULATION = "unparsed_calculation"
    DERIVED_QUANTITY = "derived_quantity"
    UNVERIFIED_AMOUNT = "unverified_amount"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    UNRELATED_OR_INSUFFICIENT_EVIDENCE = "unrelated_or_insufficient_evidence"
    ARITHMETIC_MISMATCH = "arithmetic_mismatch"
    UNRESOLVED_AUTHORITY = "unresolved_authority"
    MATCHES_COVERAGE_VERDICT = "matches_coverage_verdict"
    WHOLE_CORPUS_ABSENCE_UNPROVEN = "whole_corpus_absence_unproven"
    COVERAGE_VERDICT_UNAVAILABLE = "coverage_verdict_unavailable"
    COVERAGE_TOPIC_NOT_MATCHED = "coverage_topic_not_matched"
    COVERAGE_STATEMENT_NOT_IN_VERDICT = "coverage_statement_not_in_verdict"


class NoticeSchema(BaseModel):
    """One system-rendered notice attached to an assistant message."""

    kind: str
    language: str
    text: str
    source: dict[Any, Any] = Field(default_factory=dict)


class TerminalOutcome(BaseModel):
    """Message delivery is distinct from successful factual answering."""

    model_config = ConfigDict(extra="forbid")
    version: Literal["answer.outcome.v1"] = "answer.outcome.v1"
    outcome: Literal[
        "answered",
        "partial",
        "needs_input",
        "insufficient_evidence",
        "unresolved_authority",
        "verification_failed",
        "timed_out",
    ]
    reason_code: str = Field(max_length=100)
    failure_stage: (
        Literal["retrieval", "coverage", "draft_schema", "claim_verification", "persistence"] | None
    ) = None
    requested_scope: dict[str, Any] = Field(default_factory=dict)
    coverage: Literal["complete", "partial", "incomplete", "not_assessed"] = "not_assessed"
    retryable: bool = False
    next_action: Literal["retry", "supply_input", "review_source", "contact_operator", "none"] = (
        "none"
    )
    supported_requirement_ids: list[str] = Field(default_factory=list)
    unresolved_requirement_ids: list[str] = Field(default_factory=list)


class ProviderStageProvenance(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str
    model: str
    purpose: str = "unspecified"
    reasoning: str = "provider_default"
    schema_mode: Literal["none", "prompt", "json_object", "json_schema"] = "none"
    schema_name: str | None = None
    schema_hash: str | None = None
    endpoint_hash: str | None = None
    capability_revision: str | None = None
    capability_source: str | None = None
    local_validation: str | None = None
    span_id: str | None = None
    status: str | None = None


class MessageResponse(BaseModel):
    """Serialized message entity."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_version: str | None = None
    embedding_set_version: int | None = None
    provider: str | None = None
    model: str | None = None
    config_snapshot_id: uuid.UUID | None = None
    index_build_id: uuid.UUID | None = None
    source_metadata_generation: int | None = None
    retrieval_latency_ms: int | None = None
    provider_latency_ms: int | None = None
    total_latency_ms: int | None = None
    provider_provenance: list[ProviderStageProvenance] = Field(default_factory=list)
    citation_coverage_status: Literal["applicable", "not_applicable"] | None = None
    config_provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="message_metadata")
    citations: list[CitationSnapshot] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    grounded: bool | None = None
    insufficient_evidence_reason: InsufficientEvidenceReason | None = None
    terminal_outcome: TerminalOutcome | None = None
    notices: list[NoticeSchema] = Field(default_factory=list)
    source_provenance: SourceProvenance = SourceProvenance.NONE
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_message(
        cls,
        message: Any,
        *,
        conversation_provider: str | None = None,
        conversation_model: str | None = None,
    ) -> MessageResponse:
        base = cls.model_validate(message)
        if message.provider is None and conversation_provider is not None:
            base = base.model_copy(update={"provider": conversation_provider})
        if message.model is None and conversation_model is not None:
            base = base.model_copy(update={"model": conversation_model})
        metadata = dict(getattr(message, "message_metadata", None) or {})
        metadata.pop("operator_diagnostic", None)
        if "answer_draft" in metadata:
            metadata["answer_draft"] = public_draft_diagnostics(metadata["answer_draft"])
        provenance = metadata.get("source_provenance", SourceProvenance.NONE.value)
        try:
            source_provenance = SourceProvenance(provenance)
        except ValueError:
            source_provenance = SourceProvenance.NONE
        raw_notices = metadata.get("notices") or []
        notices = [
            NoticeSchema(**item) if isinstance(item, dict) else item
            for item in raw_notices
            if isinstance(item, (dict, NoticeSchema))
        ]
        base = base.model_copy(
            update={
                "source_provenance": source_provenance,
                "metadata": metadata,
                "provider_provenance": [
                    ProviderStageProvenance.model_validate(item)
                    for item in metadata.get("provider_provenance", [])
                ],
                "citation_coverage_status": metadata.get("citation_coverage_status"),
                "notices": notices,
                "terminal_outcome": TerminalOutcome.model_validate(metadata["terminal_outcome"])
                if isinstance(metadata.get("terminal_outcome"), dict)
                else None,
            }
        )
        return base


class MessageSendRequest(BaseModel):
    """Send a user message in a conversation."""

    preview_index_build_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=32_000)
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    as_of: datetime | None = None
    source_scope: SourceScope = SourceScope.PROJECT_DEFAULT


class ChatTurnResponse(BaseModel):
    """User + assistant messages from one chat turn."""

    user_message: MessageResponse
    assistant_message: MessageResponse


class MessageDiagnosticResponse(BaseModel):
    message_id: uuid.UUID
    project_id: uuid.UUID
    summary: dict[str, Any]
    payload: dict[str, Any] | None
    expires_at: datetime | None
    expired: bool
