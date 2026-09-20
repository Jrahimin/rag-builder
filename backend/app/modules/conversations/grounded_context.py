"""Shared knowledge-context selection used by chat and evaluation."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from typing import Any

from app.core.config import ChatConfig, EvidenceGateMode, RetrievalConfig
from app.modules.conversations.citation_snapshots import (
    EVIDENCE_PROVENANCE_VERSION,
    REUSE_VALIDATION_VERSION,
    citation_authority_records,
)
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.current_authority import (
    IRRELEVANT_AUTHORITY_OUTCOMES,
    annotate_authority_limitations,
    parse_record_date,
    record_applies_on,
    remove_superseded_provisions,
)
from app.modules.conversations.grounding_service import (
    EvidenceDecision,
    GroundingService,
    is_strict_corroboration,
)
from app.modules.conversations.ports import ContextChunk, EvidenceUnit
from app.modules.conversations.schemas.message import InsufficientEvidenceReason
from app.platform.domain.content_hash import content_hash


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
    reference_date: date | None = None,
) -> tuple[EvidenceDecision, list[ContextChunk]]:
    """Assess retrieved candidates, then select context the same way chat does.

    One admission path runs on the full retrieved set: candidate-wise when a
    reranker applied, otherwise the no-reranker fallback. Budgeting may omit an
    admitted unit but must not truncate it.

    Authority redaction is applied *before* admission using ``expansion_records``
    from the top-level retrieval diagnostics.  A chunk whose superseded provision
    spans the entire content is simply absent from the candidate set. After
    budgeting, authority is checked again against the retained context: dropping
    a required modifier must not make the surviving base rule usable.

    After the first assessment, high-confidence reranker candidates that only
    narrowly miss corroboration may receive bounded passage scoring and a
    reassessment. Always-on retrieval passage scoring already fills those
    fields, so rescue is skipped for those candidates. Rescue is additive: a
    later pass cannot drop a candidate that already satisfied the strict path.

    Observe uses the same selected admitted units as enforce. When nothing is
    admitted, observe can use ranked candidates for relevance misses. Known
    unresolved authority blocks both modes and permits recovery instead.
    """
    # --- Phase 3: redact superseded provisions before admission ---
    authority_safe_chunks = remove_superseded_provisions(
        chunks, expansion_records, reference_date=reference_date
    )

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
    knowledge_selected = annotate_authority_limitations(
        context_builder.select(ordered_units),
        expansion_records or [],
        reference_date=reference_date,
    )
    if knowledge_selected:
        aligned = _align_winner_to_selected(evidence, knowledge_selected)
        # Relevance admission does not establish current applicability. A known
        # unresolved rule must trigger recovery/refusal before generation, not
        # merely turn the answer's badge yellow after an unsafe calculation.
        if any(
            chunk.metadata.get("authority_status") == "unresolved" for chunk in knowledge_selected
        ):
            return replace(
                aligned, sufficient=False, reason=InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
            ), knowledge_selected
        return aligned, knowledge_selected
    if chat_config.evidence_gate_mode is EvidenceGateMode.OBSERVE:
        ranked_selected = context_builder.select(rescued_chunks)
        evidence = replace(evidence, observe_context="ranked_candidates")
        if ranked_selected:
            evidence = _align_winner_to_selected(
                evidence,
                ranked_selected,
                admitted=False,
            )
            if any(
                chunk.metadata.get("authority_status") == "unresolved" for chunk in ranked_selected
            ):
                evidence = replace(
                    evidence,
                    sufficient=False,
                    reason=InsufficientEvidenceReason.UNRESOLVED_AUTHORITY,
                )
        return evidence, ranked_selected
    if evidence.admitted_units and not knowledge_selected:
        evidence = replace(
            evidence,
            sufficient=False,
            reason=InsufficientEvidenceReason.CONTEXT_SELECTION_EMPTY,
        )
    return evidence, knowledge_selected


def reconstruct_reused_evidence(
    *,
    chunks: list[ContextChunk],
    citations: list[dict[str, Any]],
    expansion_records: list[dict[str, object]] | None,
    context_builder: ContextBuilder,
    current_configuration_hash: str | None = None,
    current_config_snapshot_id: uuid.UUID | None = None,
    reference_date: date | None = None,
) -> tuple[list[EvidenceUnit], dict[str, Any]]:
    """Rebuild admitted evidence units from exact recall against saved provenance."""
    diagnostics: dict[str, Any] = {
        "reuse_validation_version": REUSE_VALIDATION_VERSION,
        "reuse_validation_outcome": "failed",
        "reuse_failure_category": None,
        "reused_evidence_count": 0,
        "invalidated_identities": [],
    }
    if not citations:
        return _reuse_failure(diagnostics, "missing_provenance")
    if not chunks:
        return _reuse_failure(diagnostics, "missing_identity")
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    required: list[tuple[dict[str, Any], ContextChunk]] = []
    for citation in citations:
        if citation.get("source_kind") == "web":
            continue
        try:
            chunk_id = uuid.UUID(str(citation.get("chunk_id")))
        except (TypeError, ValueError):
            return _reuse_failure(diagnostics, "missing_identity")
        if citation.get("evidence_provenance_version") != EVIDENCE_PROVENANCE_VERSION:
            return _reuse_failure(diagnostics, "missing_provenance", [str(chunk_id)])
        chunk = by_id.get(chunk_id)
        if chunk is None:
            return _reuse_failure(diagnostics, "missing_identity", [str(chunk_id)])
        saved_config = citation.get("configuration_hash")
        current_config = current_configuration_hash or chunk.metadata.get("configuration_hash")
        if saved_config and (not current_config or saved_config != current_config):
            return _reuse_failure(diagnostics, "configuration_changed", [str(chunk_id)])
        saved_snapshot = citation.get("config_snapshot_id")
        if (
            saved_snapshot
            and current_config_snapshot_id is not None
            and not saved_config
            and str(saved_snapshot) != str(current_config_snapshot_id)
        ):
            return _reuse_failure(diagnostics, "configuration_changed", [str(chunk_id)])
        indexed_hash = citation.get("indexed_chunk_hash")
        if not indexed_hash or content_hash(chunk.content) != indexed_hash:
            return _reuse_failure(diagnostics, "hash_mismatch", [str(chunk_id)])
        saved_document = citation.get("document_id")
        if saved_document is not None and str(chunk.document_id) != str(saved_document):
            return _reuse_failure(diagnostics, "document_mismatch", [str(chunk_id)])
        if citation.get("evidence_source_envelope") == "reconstructed_context" and not (
            chunk.metadata.get("table_context")
            or chunk.metadata.get("table_row_group")
            or chunk.metadata.get("heading_context_status") == "preserved"
        ):
            return _reuse_failure(diagnostics, "unreproducible_derivation", [str(chunk_id)])
        if _unrecorded_modifier(chunk, citation):
            return _reuse_failure(diagnostics, "unrecorded_authority_dependency", [str(chunk_id)])
        required.append((citation, chunk))
    if not required:
        return _reuse_failure(diagnostics, "missing_provenance")
    current_records = list(expansion_records) if expansion_records is not None else []
    for citation, chunk in required:
        changed = _authority_meaning_changed(
            chunk, citation, current_records, reference_date=reference_date
        )
        if changed is None:
            continue
        return _reuse_failure(diagnostics, changed, [str(chunk.chunk_id)])
    required_modifiers = _required_modifier_revisions(
        (citation for citation, _ in required),
        reference_date=reference_date,
    )
    present_revisions = {
        str(value)
        for chunk in chunks
        if (value := chunk.metadata.get("source_revision_id")) is not None
    }
    missing_modifiers = [item for item in required_modifiers if item not in present_revisions]
    if missing_modifiers:
        return _reuse_failure(diagnostics, "missing_identity", missing_modifiers)
    authority_pool: list[ContextChunk] = []
    seen: set[uuid.UUID] = set()
    for _, chunk in required:
        if chunk.chunk_id not in seen:
            authority_pool.append(chunk)
            seen.add(chunk.chunk_id)
    for chunk in chunks:
        revision = str(chunk.metadata.get("source_revision_id") or "")
        if chunk.chunk_id not in seen and revision in required_modifiers:
            authority_pool.append(chunk)
            seen.add(chunk.chunk_id)
    authority_safe = {
        chunk.chunk_id: chunk
        for chunk in remove_superseded_provisions(
            authority_pool, current_records, reference_date=reference_date
        )
    }
    units: list[EvidenceUnit] = []
    for citation, raw_chunk in required:
        safe = authority_safe.get(raw_chunk.chunk_id)
        if safe is None:
            return _reuse_failure(diagnostics, "authority_redaction", [str(raw_chunk.chunk_id)])
        source_hash = citation.get("evidence_source_chunk_hash")
        if not source_hash or content_hash(safe.content) != source_hash:
            return _reuse_failure(diagnostics, "source_hash_mismatch", [str(raw_chunk.chunk_id)])
        unit = _evidence_unit_from_citation(safe, citation)
        if unit is None:
            return _reuse_failure(diagnostics, "span_mismatch", [str(raw_chunk.chunk_id)])
        units.append(unit)
    authority_chunks: list[ContextChunk] = [
        *units,
        *[
            chunk
            for chunk in authority_safe.values()
            if chunk.chunk_id not in {unit.chunk_id for unit in units}
        ],
    ]
    annotated = annotate_authority_limitations(
        authority_chunks, current_records, reference_date=reference_date
    )
    unresolved = [
        str(chunk.chunk_id)
        for chunk in annotated
        if chunk.metadata.get("authority_status") == "unresolved"
        and chunk.chunk_id in {unit.chunk_id for unit in units}
        and not _unresolved_only_unrecalled_modifiers(chunk, required)
    ]
    if unresolved:
        return _reuse_failure(diagnostics, "unresolved_authority", unresolved)
    selected_units = [chunk for chunk in annotated if isinstance(chunk, EvidenceUnit)]
    selected = context_builder.select(selected_units)
    reused = [unit for unit in selected if isinstance(unit, EvidenceUnit)]
    missing_required = [
        str(unit.chunk_id)
        for unit in units
        if unit.chunk_id not in {item.chunk_id for item in reused}
    ]
    if missing_required or len(reused) != len(units):
        return _reuse_failure(diagnostics, "context_overflow", missing_required)
    diagnostics.update(
        {
            "reuse_validation_outcome": "passed",
            "reuse_failure_category": None,
            "reused_evidence_count": len(reused),
            "invalidated_identities": [],
        }
    )
    return reused, diagnostics


def reused_evidence_decision(units: list[EvidenceUnit]) -> EvidenceDecision:
    """Admission reuse is not a new relevance contest or coverage review."""
    winner = units[0]
    return EvidenceDecision(
        sufficient=True,
        reason=None,
        winning_chunk_id=winner.chunk_id,
        admitted_units=tuple(units),
        grounding_path="exact_citation_recall",
        evidence_char_start=winner.evidence_char_start,
        evidence_char_end=winner.evidence_char_end,
        winning_rank_score=winner.rank_score,
        observe_context="reused_citations",
    )


def select_exact_recalled_knowledge(
    *,
    context_builder: ContextBuilder,
    chunks: list[ContextChunk],
    expansion_records: list[dict[str, object]] | None = None,
    reference_date: date | None = None,
) -> list[ContextChunk]:
    """Select current exact-citation recall without reapplying query admission.

    Presentation-only follow-ups can use the active passages cited by the prior
    answer even when the new formatting request is lexically unrelated to those
    passages. Authority redaction still runs before budgeting unless the caller
    already reconstructed EvidenceUnit spans. Unresolved authority remains unusable.
    """
    records = expansion_records or []
    authority_safe: list[ContextChunk]
    if chunks and all(isinstance(chunk, EvidenceUnit) for chunk in chunks):
        authority_safe = list(chunks)
    else:
        authority_safe = remove_superseded_provisions(
            chunks, records, reference_date=reference_date
        )
    selected = annotate_authority_limitations(
        context_builder.select(authority_safe),
        records,
        reference_date=reference_date,
    )
    if any(chunk.metadata.get("authority_status") == "unresolved" for chunk in selected):
        return []
    return selected


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


def _evidence_unit_from_citation(
    chunk: ContextChunk, citation: dict[str, Any]
) -> EvidenceUnit | None:
    start_raw = citation.get("evidence_chunk_char_start")
    end_raw = citation.get("evidence_chunk_char_end")
    if not isinstance(start_raw, int) or not isinstance(end_raw, int):
        return None
    start, end = start_raw, end_raw
    if start < 0 or end > len(chunk.content) or end <= start:
        return None
    span_text = chunk.content[start:end]
    span_hash = citation.get("evidence_span_hash")
    if not span_hash or content_hash(span_text) != span_hash:
        return None
    reconstructed = citation.get("evidence_source_envelope") == "reconstructed_context"
    document_start = (
        chunk.char_start
        if reconstructed
        else (chunk.char_start + start if chunk.char_start is not None else start)
    )
    document_end = (
        chunk.char_end
        if reconstructed
        else (chunk.char_start + end if chunk.char_start is not None else end)
    )
    unit_id = str(citation.get("evidence_unit_id") or "")
    derivation = str(citation.get("evidence_span_derivation") or "complete_chunk")
    corroboration = str(citation.get("evidence_corroboration_method") or "")
    metadata = {
        **chunk.metadata,
        "evidence_unit_id": unit_id,
        "evidence_span_hash": span_hash,
        "evidence_source_chunk_hash": citation.get("evidence_source_chunk_hash"),
        "indexed_chunk_hash": citation.get("indexed_chunk_hash")
        or chunk.metadata.get("indexed_chunk_hash"),
        "evidence_chunk_char_start": start,
        "evidence_chunk_char_end": end,
        "evidence_span_derivation": derivation,
        "evidence_query_variant_id": citation.get("evidence_query_variant_id") or "original",
        "evidence_corroboration_method": corroboration,
        "originating_assistant_message_id": citation.get("originating_assistant_message_id"),
        "evidence_source_envelope": citation.get("evidence_source_envelope"),
    }
    return EvidenceUnit(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        content=span_text,
        score=chunk.score,
        filename=chunk.filename,
        chunk_hash=span_hash,
        semantic_score=chunk.semantic_score,
        rank_score=chunk.rank_score,
        rerank_relevance_score=chunk.rerank_relevance_score,
        evidence_relevance_score=chunk.evidence_relevance_score,
        evidence_score_method=chunk.evidence_score_method,
        evidence_calibration_id=chunk.evidence_calibration_id,
        page_number=chunk.page_number,
        char_start=document_start,
        char_end=document_end,
        query_variants=chunk.query_variants,
        branch_contributions=chunk.branch_contributions,
        metadata=metadata,
        evidence_unit_id=unit_id,
        source_chunk_hash=str(citation.get("evidence_source_chunk_hash") or ""),
        evidence_span_hash=span_hash,
        evidence_char_start=start,
        evidence_char_end=end,
        span_derivation=derivation,
        query_variant_id=str(citation.get("evidence_query_variant_id") or "original"),
        corroboration_method=corroboration,
    )


def _reuse_failure(
    diagnostics: dict[str, Any],
    category: str,
    identities: list[str] | None = None,
) -> tuple[list[EvidenceUnit], dict[str, Any]]:
    diagnostics["reuse_validation_outcome"] = "failed"
    diagnostics["reuse_failure_category"] = category
    diagnostics["reused_evidence_count"] = 0
    diagnostics["invalidated_identities"] = list(identities or [])[:24]
    return [], diagnostics


def _unrecorded_modifier(chunk: ContextChunk, citation: dict[str, Any]) -> bool:
    recorded = {
        str(item.get("modifier_revision_id") or item.get("source_revision_id"))
        for item in citation_authority_records(citation)
        if item.get("modifier_revision_id") or item.get("source_revision_id")
    }
    current = chunk.metadata.get("source_relationships") or []
    chunk_revision = str(chunk.metadata.get("source_revision_id") or "")
    for relation in current:
        if not isinstance(relation, dict):
            continue
        if str(relation.get("relationship_type") or "").lower() != "modifies":
            continue
        direction = str(relation.get("direction") or "")
        modifier = relation.get("source_revision_id") or relation.get("modifier_revision_id")
        if (
            direction == "incoming"
            and modifier
            and str(modifier) not in recorded
            and str(modifier) != chunk_revision
        ):
            return True
        target = relation.get("target_revision_id")
        if direction != "outgoing" and target and chunk_revision and str(target) == chunk_revision:
            modifier_id = relation.get("source_revision_id")
            if modifier_id and str(modifier_id) not in recorded:
                return True
    return False


def _iso_date(value: object) -> str:
    parsed = parse_record_date(value)
    if parsed is not None:
        return parsed.isoformat()
    if value is None or value == "":
        return ""
    return str(value).strip()[:10]


def _authority_meaning(record: dict[str, Any]) -> tuple[str, ...]:
    provisions = record.get("target_provisions") or []
    scoped = tuple(sorted(str(item) for item in provisions if item))
    return (
        str(record.get("relationship_id") or ""),
        str(record.get("base_revision_id") or ""),
        str(record.get("modifier_revision_id") or record.get("source_revision_id") or ""),
        _iso_date(record.get("base_effective_from")),
        _iso_date(record.get("base_effective_to")),
        _iso_date(record.get("modifier_effective_from")),
        _iso_date(record.get("modifier_effective_to")),
        *scoped,
    )


def _current_authority_meanings(
    records: list[dict[str, Any]],
    chunk: ContextChunk,
    *,
    reference_date: date | None = None,
) -> set[tuple[str, ...]]:
    revision = str(chunk.metadata.get("source_revision_id") or "")
    meanings: set[tuple[str, ...]] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("outcome") or "") in IRRELEVANT_AUTHORITY_OUTCOMES:
            continue
        if not record_applies_on(record, reference_date=reference_date):
            continue
        base = str(record.get("base_revision_id") or "")
        modifier = str(record.get("modifier_revision_id") or "")
        if not revision or (base != revision and modifier != revision):
            continue
        meanings.add(_authority_meaning(record))
    return meanings


def _saved_authority_meanings(
    citation: dict[str, Any], *, reference_date: date | None = None
) -> set[tuple[str, ...]]:
    meanings: set[tuple[str, ...]] = set()
    for item in citation_authority_records(citation):
        if str(item.get("outcome") or "") in IRRELEVANT_AUTHORITY_OUTCOMES:
            continue
        if not record_applies_on(item, reference_date=reference_date):
            continue
        meanings.add(_authority_meaning(item))
    return meanings


def _authority_meaning_changed(
    chunk: ContextChunk,
    citation: dict[str, Any],
    current_records: list[dict[str, Any]],
    *,
    reference_date: date | None = None,
) -> str | None:
    """Compare saved admission provenance to current metadata. Saved records never authorize."""
    saved = _saved_authority_meanings(citation, reference_date=reference_date)
    current = _current_authority_meanings(current_records, chunk, reference_date=reference_date)
    if saved == current:
        return None
    extra = current - saved
    missing = saved - current
    if extra and not missing:
        return "unrecorded_authority_dependency"
    return "authority_dependency_changed"


_REQUIRED_MODIFIER_OUTCOMES = {"expanded", "already_in_recall"}


def _required_modifier_revisions(
    citations: Iterable[dict[str, Any]],
    *,
    reference_date: date | None = None,
) -> list[str]:
    needed: list[str] = []
    seen: set[str] = set()
    for citation in citations:
        for record in citation_authority_records(citation):
            modifier = str(record.get("modifier_revision_id") or "")
            if not modifier or modifier in seen:
                continue
            if str(record.get("outcome") or "") in IRRELEVANT_AUTHORITY_OUTCOMES:
                continue
            if not record_applies_on(record, reference_date=reference_date):
                continue
            recalled = record.get("modifier_recalled")
            outcome = str(record.get("outcome") or "")
            if recalled is True or (recalled is None and outcome in _REQUIRED_MODIFIER_OUTCOMES):
                seen.add(modifier)
                needed.append(modifier)
    return needed


def _unresolved_only_unrecalled_modifiers(
    chunk: ContextChunk,
    required: list[tuple[dict[str, Any], ContextChunk]],
) -> bool:
    limitations = chunk.metadata.get("authority_limitations") or []
    if not limitations:
        return False
    citation = next(
        (item for item, raw in required if raw.chunk_id == chunk.chunk_id),
        None,
    )
    if citation is None:
        return False
    unrecalled = {
        str(record.get("modifier_revision_id") or "")
        for record in citation_authority_records(citation)
        if record.get("modifier_recalled") is False
    }
    for limitation in limitations:
        if not isinstance(limitation, dict):
            return False
        if limitation.get("reason") != "modifier_absent_from_context":
            return False
        if str(limitation.get("modifier_revision_id") or "") not in unrecalled:
            return False
    return True
