"""Pydantic schemas for messages."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.message import MessageRole


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
    relationship_recall_provenance: list[dict[str, Any]] = Field(default_factory=list)
    config_snapshot_id: uuid.UUID | None = None
    configuration_hash: str | None = None
    config_provenance: dict[str, Any] = Field(default_factory=dict)
    prompt_version: str | None = None
    web_url: str | None = None
    web_title: str | None = None
    web_retrieved_at: datetime | None = None
    web_provider: str | None = None

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
    # Historical persisted value. The semantic-only rejection gate never emits it.
    LOW_QUERY_EVIDENCE_COVERAGE = "low_query_evidence_coverage"
    AUTHORITY_CONTEXT_EMPTY = "authority_context_empty"
    CONTEXT_SELECTION_EMPTY = "context_selection_empty"


class ClaimVerification(StrEnum):
    """What the deterministic validator can establish about one claim."""

    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"


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
        metadata = getattr(message, "message_metadata", None) or {}
        provenance = metadata.get("source_provenance", SourceProvenance.NONE.value)
        try:
            source_provenance = SourceProvenance(provenance)
        except ValueError:
            source_provenance = SourceProvenance.NONE
        base = base.model_copy(update={"source_provenance": source_provenance})
        return base


class MessageSendRequest(BaseModel):
    """Send a user message in a conversation."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=32_000)
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    as_of: datetime | None = None


class ChatTurnResponse(BaseModel):
    """User + assistant messages from one chat turn."""

    user_message: MessageResponse
    assistant_message: MessageResponse
