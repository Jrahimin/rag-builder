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
) -> tuple[EvidenceDecision, list[ContextChunk]]:
    """Assess retrieved candidates, then select context the same way chat does.

    Candidate-wise admission runs on the full retrieved set. Budgeting may omit
    an admitted unit but must not truncate it. Legacy generation still uses the
    existing truncated selection so default-off behavior stays compatible.

    After the first assessment, high-confidence reranker candidates that only
    narrowly miss corroboration may receive bounded passage scoring and a
    reassessment. Always-on retrieval passage scoring already fills those
    fields, so rescue is skipped for those candidates. Rescue is additive: a
    later pass cannot drop a candidate that already satisfied the strict path.

    Authority redaction is applied after admission. If the highest-ranked
    admitted unit becomes unusable, selection continues with the next valid
    admitted unit rather than blocking generation.
    """
    candidate_evidence = grounding.assess_candidate_wise(
        question,
        chunks,
        rerank_status=rerank_status,
    )
    rescue_ids = grounding.passage_rescue_chunk_ids(
        chunks,
        candidate_evidence.candidate_assessments,
    )
    rescue_status = "not_needed"
    rescued_chunks = chunks
    if rescue_ids:
        window_tokens = retrieval_config.passage_window_tokens if retrieval_config else 96
        overlap_tokens = retrieval_config.passage_overlap_tokens if retrieval_config else 24
        min_tokens = retrieval_config.passage_min_tokens if retrieval_config else 32
        rescued_chunks, rescue_status = await grounding.apply_passage_rescue(
            question,
            chunks,
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
        candidate_evidence = grounding.merge_monotonic_admissions(
            candidate_evidence,
            rescued_evidence,
        )
    candidate_evidence = replace(
        candidate_evidence,
        passage_rescue_status=rescue_status,
        passage_rescue_candidate_count=len(rescue_ids),
    )

    authority_safe_chunks = remove_superseded_provisions(rescued_chunks)
    legacy_selected = context_builder.select(authority_safe_chunks)
    legacy_evidence = grounding.assess_legacy(
        question,
        legacy_selected,
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
            passage_rescue_status=candidate_evidence.passage_rescue_status,
            passage_rescue_candidate_count=candidate_evidence.passage_rescue_candidate_count,
        )
        return evidence, legacy_selected

    evidence = replace(
        candidate_evidence,
        legacy_sufficient=legacy_evidence.sufficient,
        legacy_winning_chunk_id=legacy_evidence.winning_chunk_id,
    )
    if evidence.grounding_path != "candidate_wise":
        return evidence, legacy_selected

    usable_units = _usable_admitted_units(evidence.admitted_units, authority_safe_chunks)
    ordered_units = _monotonic_context_order(usable_units)
    knowledge_selected = context_builder.select(ordered_units)
    evidence = replace(evidence, usable_after_authority_count=len(usable_units))
    if not knowledge_selected and chat_config.evidence_gate_mode is EvidenceGateMode.OBSERVE:
        return evidence, legacy_selected
    if evidence.admitted_units and not knowledge_selected:
        reason = (
            InsufficientEvidenceReason.AUTHORITY_CONTEXT_EMPTY
            if not usable_units
            else InsufficientEvidenceReason.CONTEXT_SELECTION_EMPTY
        )
        evidence = replace(evidence, sufficient=False, reason=reason)
        return evidence, knowledge_selected
    if knowledge_selected:
        evidence = _align_winner_to_selected(evidence, knowledge_selected)
    return evidence, knowledge_selected


def _usable_admitted_units(
    admitted_units: tuple[EvidenceUnit, ...],
    authority_safe_chunks: list[ContextChunk],
) -> list[EvidenceUnit]:
    """Keep admitted spans whose text still exists after provision redaction."""
    redacted = {chunk.chunk_id: chunk for chunk in authority_safe_chunks}
    usable: list[EvidenceUnit] = []
    for unit in admitted_units:
        source = redacted.get(unit.chunk_id)
        if source is None or not _admitted_span_survives_authority(unit, source):
            continue
        usable.append(unit)
    return usable


def _admitted_span_survives_authority(unit: EvidenceUnit, redacted: ContextChunk) -> bool:
    start = unit.evidence_char_start
    end = unit.evidence_char_end
    if not (0 <= start < end <= len(redacted.content)):
        return False
    redacted_span = redacted.content[start:end]
    return "".join(redacted_span.split()) == "".join(unit.content.split())


def _monotonic_context_order(units: list[EvidenceUnit]) -> list[EvidenceUnit]:
    """Prefer strict admissions so balanced-only extras cannot crowd them out."""
    strict = [unit for unit in units if is_strict_corroboration(unit.corroboration_method)]
    additive = [unit for unit in units if not is_strict_corroboration(unit.corroboration_method)]
    return strict + additive


def _align_winner_to_selected(
    evidence: EvidenceDecision,
    selected: list[ContextChunk],
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
    return replace(
        evidence,
        sufficient=True,
        reason=None,
        winning_chunk_id=winner.chunk_id,
        best_score=(
            matching.reranker_score if matching is not None else evidence.best_score
        ),
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
        winning_rank_score=(
            unit.rank_score if unit is not None else evidence.winning_rank_score
        ),
    )
