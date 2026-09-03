"""Shared knowledge-context selection used by chat and evaluation."""

from __future__ import annotations

from dataclasses import replace

from app.core.config import ChatConfig, EvidenceGateMode, RetrievalConfig
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.current_authority import remove_superseded_provisions
from app.modules.conversations.grounding_service import (
    EvidenceDecision,
    GroundingService,
    is_strict_corroboration,
)
from app.modules.conversations.ports import ContextChunk, EvidenceUnit
from app.modules.conversations.schemas.message import InsufficientEvidenceReason


async def assess_and_select_knowledge(
    *,
    grounding: GroundingService,
    context_builder: ContextBuilder,
    chat_config: ChatConfig,
    question: str,
    chunks: list[ContextChunk],
    rerank_status: str | None,
    retrieval_config: RetrievalConfig | None = None,
    expansion_records: list[dict[str, object]] | None = None,
) -> tuple[EvidenceDecision, list[ContextChunk]]:
    """Assess retrieved candidates, then select context the same way chat does.

    One admission path runs on the full retrieved set: candidate-wise when a
    reranker applied, otherwise the no-reranker fallback. Budgeting may omit an
    admitted unit but must not truncate it.

    Authority redaction is applied *before* admission using ``expansion_records``
    from the top-level retrieval diagnostics.  A chunk whose superseded provision
    spans the entire content is simply absent from the candidate set; there is no
    post-admission reconciliation step.  If the modifier revision is not present
    among the retrieved chunks, redaction is skipped (``not_applicable``).

    After the first assessment, high-confidence reranker candidates that only
    narrowly miss corroboration may receive bounded passage scoring and a
    reassessment. Always-on retrieval passage scoring already fills those
    fields, so rescue is skipped for those candidates. Rescue is additive: a
    later pass cannot drop a candidate that already satisfied the strict path.

    Observe uses the same selected admitted units as enforce. When nothing is
    admitted, observe still generates from ranked candidates and records
    would-have-blocked rather than substituting a different winner path.
    """
    # --- Phase 3: redact superseded provisions before admission ---
    authority_safe_chunks = remove_superseded_provisions(chunks, expansion_records)

    evidence = grounding.assess(question, authority_safe_chunks, rerank_status=rerank_status)
    rescued_chunks = authority_safe_chunks
    rescue_ids = (
        grounding.passage_rescue_chunk_ids(authority_safe_chunks, evidence.candidate_assessments)
        if evidence.grounding_path == "candidate_wise"
        else []
    )
    rescue_status = "not_needed"
    if rescue_ids:
        window_tokens = retrieval_config.passage_window_tokens if retrieval_config else 96
        overlap_tokens = retrieval_config.passage_overlap_tokens if retrieval_config else 24
        min_tokens = retrieval_config.passage_min_tokens if retrieval_config else 32
        rescued_chunks, rescue_status = await grounding.apply_passage_rescue(
            question,
            authority_safe_chunks,
            rescue_ids,
            window_tokens=window_tokens,
            overlap_tokens=overlap_tokens,
            min_tokens=min_tokens,
        )
        rescued_evidence = grounding.assess_candidate_wise(
            question,
            rescued_chunks,
            rerank_status=rerank_status,
        )
        evidence = grounding.merge_monotonic_admissions(
            evidence,
            rescued_evidence,
        )
    evidence = replace(
        evidence,
        passage_rescue_status=rescue_status,
        passage_rescue_candidate_count=len(rescue_ids),
    )

    ordered_units = _monotonic_context_order(list(evidence.admitted_units))
    knowledge_selected = context_builder.select(ordered_units)
    if knowledge_selected:
        return _align_winner_to_selected(evidence, knowledge_selected), knowledge_selected
    if chat_config.evidence_gate_mode is EvidenceGateMode.OBSERVE:
        ranked_selected = context_builder.select(rescued_chunks)
        evidence = replace(evidence, observe_context="ranked_candidates")
        if ranked_selected:
            evidence = _align_winner_to_selected(
                evidence,
                ranked_selected,
                admitted=False,
            )
        return evidence, ranked_selected
    if evidence.admitted_units and not knowledge_selected:
        evidence = replace(
            evidence,
            sufficient=False,
            reason=InsufficientEvidenceReason.CONTEXT_SELECTION_EMPTY,
        )
    return evidence, knowledge_selected


def _monotonic_context_order(units: list[EvidenceUnit]) -> list[EvidenceUnit]:
    """Prefer strict admissions so balanced-only extras cannot crowd them out."""
    strict = [unit for unit in units if is_strict_corroboration(unit.corroboration_method)]
    additive = [unit for unit in units if not is_strict_corroboration(unit.corroboration_method)]
    return strict + additive


def _align_winner_to_selected(
    evidence: EvidenceDecision,
    selected: list[ContextChunk],
    *,
    admitted: bool = True,
) -> EvidenceDecision:
    winner = selected[0]
    matching = next(
        (item for item in evidence.candidate_assessments if item.chunk_id == winner.chunk_id),
        None,
    )
    unit = next(
        (item for item in evidence.admitted_units if item.chunk_id == winner.chunk_id),
        None,
    )
    best_score = evidence.best_score
    if matching is not None:
        best_score = (
            matching.reranker_score
            if matching.reranker_score is not None
            else matching.original_semantic_score
        )
    return replace(
        evidence,
        sufficient=True if admitted else evidence.sufficient,
        reason=None if admitted else evidence.reason,
        winning_chunk_id=winner.chunk_id,
        best_score=best_score,
        query_token_coverage=(
            max(
                [
                    matching.original_lexical_coverage,
                    *matching.translated_lexical_coverage.values(),
                ]
            )
            if matching is not None
            else evidence.query_token_coverage
        ),
        lexically_corroborated=(
            matching.corroboration_method in {"original_lexical", "translated_lexical"}
            if matching is not None
            else evidence.lexically_corroborated
        ),
        evidence_char_start=(
            unit.evidence_char_start if unit is not None else evidence.evidence_char_start
        ),
        evidence_char_end=(
            unit.evidence_char_end if unit is not None else evidence.evidence_char_end
        ),
        winning_semantic_score=(
            matching.original_semantic_score
            if matching is not None
            else evidence.winning_semantic_score
        ),
        winning_rank_score=(unit.rank_score if unit is not None else winner.rank_score),
    )
