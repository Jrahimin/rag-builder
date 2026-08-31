"""Shared knowledge-context selection used by chat and evaluation."""

from __future__ import annotations

from dataclasses import replace

from app.core.config import ChatConfig, EvidenceGateMode
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.grounding_service import EvidenceDecision, GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import InsufficientEvidenceReason


def assess_and_select_knowledge(
    *,
    grounding: GroundingService,
    context_builder: ContextBuilder,
    chat_config: ChatConfig,
    question: str,
    chunks: list[ContextChunk],
    rerank_status: str | None,
) -> tuple[EvidenceDecision, list[ContextChunk]]:
    """Assess retrieved candidates, then select context the same way chat does.

    Candidate-wise admission runs on the full retrieved set. Budgeting may omit
    an admitted unit but must not truncate it. Legacy generation still uses the
    existing truncated selection so default-off behavior stays compatible.
    """
    legacy_selected = context_builder.select(chunks)
    legacy_evidence = grounding.assess_legacy(
        question,
        legacy_selected,
        rerank_status=rerank_status,
    )
    candidate_evidence = grounding.assess_candidate_wise(
        question,
        chunks,
        rerank_status=rerank_status,
    )
    if not chat_config.candidate_wise_grounding_enabled:
        evidence = replace(
            legacy_evidence,
            grounding_path="legacy_shadow",
            candidate_assessments=candidate_evidence.candidate_assessments,
            admitted_units=candidate_evidence.admitted_units,
            shadow_candidate_wise_sufficient=candidate_evidence.sufficient,
            shadow_candidate_wise_winning_chunk_id=candidate_evidence.winning_chunk_id,
            shadow_candidate_wise_admitted_count=len(candidate_evidence.admitted_units),
        )
        return evidence, legacy_selected

    evidence = replace(
        candidate_evidence,
        legacy_sufficient=legacy_evidence.sufficient,
        legacy_winning_chunk_id=legacy_evidence.winning_chunk_id,
    )
    if evidence.grounding_path != "candidate_wise":
        return evidence, legacy_selected

    knowledge_selected = context_builder.select(list(evidence.admitted_units))
    if not knowledge_selected and chat_config.evidence_gate_mode is EvidenceGateMode.OBSERVE:
        return evidence, legacy_selected
    if evidence.sufficient and not knowledge_selected:
        evidence = replace(
            evidence,
            sufficient=False,
            reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
        )
    return evidence, knowledge_selected
