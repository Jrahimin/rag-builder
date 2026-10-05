"""Source-bound offline decisions; execution and live certification are separate."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.knowledge.scope_facts import span_hash


class ReconciliationProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: uuid.UUID
    document_id: uuid.UUID
    source_revision_id: uuid.UUID
    index_build_id: uuid.UUID
    chunk_id: uuid.UUID
    source_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    chunk_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    quote: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    reason: str = Field(min_length=1)
    relationship: Literal["unknown", "modifies", "replaces", "describes"] = "unknown"
    effect: Literal["unknown", "operative", "proposal", "example"] = "unknown"
    target_provisions: tuple[str, ...] = ()
    target_revision_id: uuid.UUID | None = None
    effective_period: str | None = None


class ReconciliationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: Literal["source.reconciliation.v1"] = "source.reconciliation.v1"
    action: Literal["re_attest", "private_reprocess", "reacquire_reparse", "unresolved"]
    proof: ReconciliationProof
    obligations: tuple[str, ...]
    execution_status: Literal["pending"] = "pending"


def decide_reconciliation(
    proof: ReconciliationProof,
    *,
    chunk_content: str,
    current_source_hash: str,
    proposed_source_hash: str | None,
    structure_changed: bool,
    missing_components: tuple[str, ...] = (),
) -> ReconciliationDecision:
    """Match saved proof before planning; never fill dates or legal effects from titles."""
    if (
        span_hash(chunk_content) != proof.chunk_hash
        or current_source_hash != proof.source_content_hash
        or chunk_content[proof.char_start : proof.char_end] != proof.quote
    ):
        raise ValueError("Reconciliation proof does not match the exact source identity/span")
    obligations = list(missing_components)
    if proof.relationship in {"modifies", "replaces"} and (
        proof.effect == "unknown" or proof.target_revision_id is None or not proof.target_provisions
    ):
        obligations.append("review_exact_relationship_effect_and_target_scope")
    if proof.effective_period is None:
        obligations.append("governing_period_unestablished")
    action: Literal["re_attest", "private_reprocess", "reacquire_reparse", "unresolved"]
    if missing_components:
        action = "reacquire_reparse"
    elif proposed_source_hash is None:
        action = "unresolved"
        obligations.append("proposed_source_identity_unavailable")
    elif structure_changed or proposed_source_hash != current_source_hash:
        action = "private_reprocess"
    else:
        action = "re_attest"
    return ReconciliationDecision(action=action, proof=proof, obligations=tuple(obligations))
