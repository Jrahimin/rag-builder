"""Pydantic schemas for messages."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.message import MessageRole


class CitationSnapshot(BaseModel):
    """Durable citation stored on assistant messages."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    score: float
    chunk_hash: str
    excerpt: str | None = None


class InsufficientEvidenceReason(StrEnum):
    """Stable reasons for a correct no-answer outcome."""

    NO_RETRIEVAL_RESULTS = "no_retrieval_results"
    BELOW_RELEVANCE_THRESHOLD = "below_relevance_threshold"
    LOW_QUERY_EVIDENCE_COVERAGE = "low_query_evidence_coverage"


class ClaimEvidence(BaseModel):
    """One source location supporting an answer claim."""

    citation_index: int = Field(ge=1)
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    excerpt: str | None = None


class AnswerClaim(BaseModel):
    """A generated answer segment linked to zero or more evidence locations."""

    claim_id: str
    text: str
    grounded: bool
    evidence: list[ClaimEvidence] = Field(default_factory=list)


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
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="message_metadata")
    citations: list[CitationSnapshot] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    grounded: bool | None = None
    insufficient_evidence_reason: InsufficientEvidenceReason | None = None
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
        return base


class MessageSendRequest(BaseModel):
    """Send a user message in a conversation."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=32_000)
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)


class ChatTurnResponse(BaseModel):
    """User + assistant messages from one chat turn."""

    user_message: MessageResponse
    assistant_message: MessageResponse
