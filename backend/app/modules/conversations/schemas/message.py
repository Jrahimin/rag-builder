"""Pydantic schemas for messages."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.message import MessageRole
from app.modules.conversations.answer_draft import public_draft_diagnostics


class CitationSourceKind(StrEnum):
    """Origin of one citation snapshot."""

    KNOWLEDGE = "knowledge"
    WEB = "web"


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


class CitationSnapshot(BaseModel):
    """Durable citation stored on assistant messages."""

    source_kind: CitationSourceKind = CitationSourceKind.KNOWLEDGE
    chunk_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    filename: str
    chunk_index: int | None = None
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    score: float | None = None
    chunk_hash: str | None = None
    evidence_unit_id: str | None = None
    evidence_span_hash: str | None = None
    evidence_chunk_char_start: int | None = None
    evidence_chunk_char_end: int | None = None
    evidence_span_derivation: str | None = None
    evidence_query_variant_id: str | None = None
    excerpt: str | None = None
    supporting_spans: list[dict[str, Any]] = Field(default_factory=list)
    structural_context: list[str] = Field(default_factory=list)
    provenance_precision: str | None = None
    processing_version: int | None = None
    index_build_id: uuid.UUID | None = None
    source_metadata_generation: int | None = None
    source_revision_id: uuid.UUID | None = None
    source_group_id: uuid.UUID | None = None
    source_title: str | None = None
    source_type: str | None = None
    source_revision_number: int | None = None
    source_revision_label: str | None = None
    source_published_date: date | None = None
    source_effective_from: date | None = None
    source_effective_to: date | None = None
    source_lifecycle_status: str | None = None
    source_role: str | None = None
    source_relationships: list[dict[str, Any]] = Field(default_factory=list)
    authority_status: str | None = None
    authority_limitations: list[dict[str, Any]] = Field(default_factory=list)
    relationship_recall_provenance: list[dict[str, Any]] = Field(default_factory=list)
    authority_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    config_snapshot_id: uuid.UUID | None = None
    configuration_hash: str | None = None
    config_provenance: dict[str, Any] = Field(default_factory=dict)
    prompt_version: str | None = None
    web_url: str | None = None
    web_title: str | None = None
    web_retrieved_at: datetime | None = None
    web_provider: str | None = None
    evidence_provenance_version: str | None = None
    indexed_chunk_hash: str | None = None
    evidence_source_chunk_hash: str | None = None
    evidence_corroboration_method: str | None = None
    evidence_source_envelope: str | None = None
    evidence_scope_document_id: uuid.UUID | None = None
    evidence_scope_metadata_filter: dict[str, str] | None = None
    evidence_scope_as_of: datetime | None = None
    evidence_scope_snapshot_origin: str | None = None
    originating_assistant_message_id: uuid.UUID | None = None
    coverage_origin_message_id: uuid.UUID | None = None
    coverage_status: str | None = None
    coverage_partial: bool | None = None

    @model_validator(mode="after")
    def validate_source_identity(self) -> CitationSnapshot:
        internal_identity = (self.chunk_id, self.document_id, self.project_id)
        web_metadata = (
            self.web_url,
            self.web_title,
            self.web_retrieved_at,
            self.web_provider,
        )
        if self.source_kind is CitationSourceKind.KNOWLEDGE:
            if any(value is None for value in internal_identity):
                raise ValueError(
                    "knowledge citations require Project, document, and chunk identity"
                )
            if any(value is not None for value in web_metadata):
                raise ValueError("knowledge citations cannot carry web source metadata")
        else:
            if any(value is not None for value in internal_identity):
                raise ValueError("web citations cannot expose internal document or chunk identity")
            if self.chunk_index is not None or self.chunk_hash is not None:
                raise ValueError("web citations cannot expose synthetic chunk identity")
            if any(value is None for value in web_metadata):
                raise ValueError("web citations require URL, title, retrieval time, and provider")
        return self


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


class ClaimVerification(StrEnum):
    """What the deterministic validator can establish about one claim."""

    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"


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


class ClaimEvidence(BaseModel):
    """One source location supporting an answer claim."""

    citation_index: int = Field(ge=1)
    chunk_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    filename: str
    chunk_index: int | None = None
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    excerpt: str | None = None
    evidence_unit_id: str | None = None
    evidence_span_hash: str | None = None
    source_kind: CitationSourceKind = CitationSourceKind.KNOWLEDGE
    web_url: str | None = None
    web_title: str | None = None
    web_retrieved_at: datetime | None = None
    web_provider: str | None = None

    @model_validator(mode="after")
    def validate_source_identity(self) -> ClaimEvidence:
        if self.source_kind is CitationSourceKind.KNOWLEDGE:
            if self.chunk_id is None or self.document_id is None or self.chunk_index is None:
                raise ValueError("knowledge claim evidence requires internal source identity")
            if any(
                value is not None
                for value in (
                    self.web_url,
                    self.web_title,
                    self.web_retrieved_at,
                    self.web_provider,
                )
            ):
                raise ValueError("knowledge claim evidence cannot carry web source metadata")
        else:
            if (
                self.chunk_id is not None
                or self.document_id is not None
                or self.chunk_index is not None
            ):
                raise ValueError("web claim evidence cannot expose synthetic chunk identity")
            if any(
                value is None
                for value in (
                    self.web_url,
                    self.web_title,
                    self.web_retrieved_at,
                    self.web_provider,
                )
            ):
                raise ValueError(
                    "web claim evidence requires URL, title, retrieval time, and provider"
                )
        return self


class AnswerClaim(BaseModel):
    """A generated answer segment linked to zero or more evidence locations."""

    evidence_support: ClaimVerification | None = None
    authority_status: str = "not_assessed"
    claim_kind: str = "source_assertion"
    arithmetic_verification: ClaimVerification | None = None
    verification_method: str | None = None
    verification_reason: str | None = None
    assertion_text: str | None = None

    requirement_ids: list[str] = Field(default_factory=list)
    claim_id: str
    text: str
    grounded: bool
    verification: ClaimVerification
    evidence: list[ClaimEvidence] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def backfill_legacy_verification(cls, value: Any) -> Any:
        if isinstance(value, dict) and "verification" not in value:
            value = {
                **value,
                "verification": (
                    ClaimVerification.SUPPORTED
                    if value.get("grounded")
                    else ClaimVerification.UNSUPPORTED
                ),
            }
        return value


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
