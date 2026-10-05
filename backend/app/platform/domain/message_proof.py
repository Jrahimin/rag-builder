"""Shared public citation and assertion proof contract for Messages and acceptance.

Conversation APIs re-export these types; acceptance consumes the same schema defaults
and source-identity validators without importing another feature module.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class CitationSourceKind(StrEnum):
    """Origin of one citation snapshot."""

    KNOWLEDGE = "knowledge"
    WEB = "web"


class ClaimVerification(StrEnum):
    """What the deterministic validator can establish about one claim."""

    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"


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
    verifier_failures: list[dict[str, Any]] = Field(default_factory=list)
    calculation_references: list[str] = Field(default_factory=list)

    requirement_ids: list[str] = Field(default_factory=list)
    claim_id: str
    assertion_id: str | None = None
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
