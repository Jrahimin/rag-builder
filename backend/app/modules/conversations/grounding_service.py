"""Deterministic evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

import regex

from app.core.config import ChatConfig, EvidenceGateMode, GroundingMode
from app.modules.conversations.ports import (
    CandidateEvidenceAssessment,
    ContextChunk,
    EvidenceUnit,
)
from app.modules.conversations.schemas.message import (
    AnswerClaim,
    CitationSourceKind,
    ClaimEvidence,
    ClaimVerification,
    InsufficientEvidenceReason,
)
from app.platform.domain.content_hash import content_hash
from app.platform.domain.evidence_contracts import (
    RERANKER_RELEVANCE_CALIBRATION_ID,
    QueryVariant,
    QueryVariantKind,
)
from app.platform.domain.language_detection import ROMANIZED_BANGLA_PARTICLES, detect_language
from app.platform.domain.text_tokenization import tokenize
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingPurpose
from app.platform.providers.embedding_similarity import cosine_similarity, score_best_passages
from app.platform.providers.errors import ProviderError

_SEGMENT_PATTERN = regex.compile(
    r"(?<=[.!?।॥。\uff01\uff1f…])\s+|\n+",
    regex.UNICODE,
)
_CITATION_PATTERN = regex.compile(r"\[(\d+)\]")
_LEADING_CITATIONS_PATTERN = regex.compile(r"^((?:\[\d+\]\s*)+)(.*)$", regex.DOTALL)
_MARKDOWN_HEADING_PATTERN = regex.compile(r"^#{1,6}\s+\S.*$")
_MARKDOWN_ORDINAL_PATTERN = regex.compile(r"^(?:[-*+]\s*)?\p{Number}+[.)]?$")
_MARKDOWN_TABLE_DIVIDER_PATTERN = regex.compile(r"^\|?[\s:|-]+\|?$")
_LIST_PREAMBLE_PATTERN = regex.compile(
    r"^[^.\n!?।॥。\uff01\uff1f…]+[:：—–]\s*$",  # noqa: RUF001
)
_POLARITY_PATTERN = regex.compile(r"^(?:yes|no|না|হ্যাঁ)[.\u0964]?\s*$", regex.IGNORECASE)
_SHORT_STANCE_PATTERN = regex.compile(
    r"^(?:the\s+)?(?:claim|statement|assertion|premise)\s+"
    r"(?:is|was)\s+(?:incorrect|false|wrong|not\s+correct)[.!]?$",
    regex.IGNORECASE,
)
_SPAN_BOUNDARY_PATTERN = regex.compile(
    r"\n+|(?<=[.!?।॥。\uff01\uff1f…])\s+",
    regex.UNICODE,
)
_INSUFFICIENCY_MARKER = "not enough indexed evidence"
# _SOURCE_NOTICE_MARKERS removed in Phase 3: web-fallback notice text is no longer
# prepended to answer content; it is a structured Notice metadata field instead.
_MAX_CITATION_INHERITANCE_STRUCTURAL_GAP = 1
_MAX_CITATION_INHERITANCE_LIST_GAP = 8
_BENGALI_DIGIT_FOLD = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_MARKDOWN_LIST_ITEM_PATTERN = regex.compile(r"^[-*+]\s+\S")
_QUANTITY_SETUP_PATTERN = regex.compile(
    r"^(?:[-*+]\s+)?.+[:：]\s*(?:[A-Za-z]{1,6}\s+)*[\d,.]+\s*"  # noqa: RUF001
    r"(?:[A-Za-z\p{Bengali}]{0,12})?\s*$"
)
_CURRENCY_TOKEN = r"(?:[A-Za-z]{1,6}\s+)*"
_AMOUNT_PATTERN = regex.compile(r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?")
_EVIDENCE_RATE_PATTERN = regex.compile(r"(\d+(?:\.\d+)?)\s*%")
_CALCULATION_PATTERN = regex.compile(
    r"(?P<base>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*[×x*]\s*"  # noqa: RUF001
    r"(?P<rate>\d+(?:\.\d+)?)\s*%\s*=\s*"
    rf"{_CURRENCY_TOKEN}"
    r"(?P<result>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)",
    regex.IGNORECASE,
)
_CALCULATION_OPERATOR_PATTERN = regex.compile(
    r"[%＝=×]|[x*]\s*\d|\d\s*%",  # noqa: RUF001
    regex.IGNORECASE,
)
_ENGLISH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}
# Conservative Bangla interrogative/copula scaffolding, analogous to English
# stopwords. No stemming and no domain vocabulary.
_BANGLA_QUERY_SCAFFOLDING = {
    "কি",
    "কী",
    "কেন",
    "কিভাবে",
    "কীভাবে",
    "কোন",
    "কোথায়",
    "কোথায়",
    "কে",
    "কখন",
    "কিসের",
    "কত",
    "কতো",
    "এবং",
    "বা",
    "যে",
    "এই",
    "সে",
    "থেকে",
    "জন্য",
    "মধ্যে",
    "একটি",
    "না",
    "হ্যাঁ",
    "আছে",
    "ছিল",
    "হবে",
    "করে",
    "করা",
}
_QUERY_SCAFFOLDING = _ENGLISH_STOPWORDS | _BANGLA_QUERY_SCAFFOLDING | ROMANIZED_BANGLA_PARTICLES
_CORROBORATION_NEAR_MISS_MARGIN = 0.08
_PASSAGE_RESCUE_MAX_CANDIDATES = 4
_STRICT_CORROBORATION_METHODS = frozenset(
    {
        "original_semantic",
        "cross_language_semantic",
        "original_lexical",
        "translated_lexical",
    }
)
_CONTEXT_SELECTION_REASONS = frozenset(
    {
        # AUTHORITY_CONTEXT_EMPTY was removed in Phase 3: authority redaction now
        # happens before admission so a redacted chunk is simply absent and cannot
        # cause a post-admission empty selection.
        InsufficientEvidenceReason.CONTEXT_SELECTION_EMPTY,
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    sufficient: bool
    reason: InsufficientEvidenceReason | None = None
    query_token_coverage: float = 0.0
    best_score: float | None = None
    lexically_corroborated: bool = False
    winning_chunk_id: uuid.UUID | None = None
    evidence_score_method: str = "whole_chunk_cosine"
    evidence_calibration_id: str = "whole_chunk_cosine:v1"
    evidence_char_start: int | None = None
    evidence_char_end: int | None = None
    winning_semantic_score: float | None = None
    winning_rank_score: float | None = None
    admitted_units: tuple[EvidenceUnit, ...] = ()
    candidate_assessments: tuple[CandidateEvidenceAssessment, ...] = ()
    grounding_path: str = "no_reranker"
    passage_rescue_status: str | None = None
    passage_rescue_candidate_count: int = 0
    observe_context: str | None = None


@dataclass(frozen=True, slots=True)
class GroundingResult:
    claims: list[dict]
    grounded: bool | None
    citation_coverage: float
    unverified_claim_rate: float = 0.0
    claims_status: str | None = None


@dataclass(frozen=True, slots=True)
class _ClaimDraft:
    index: int
    text: str
    evidence_chunks: list[tuple[int, ContextChunk]]
    has_valid_citation: bool


@dataclass(frozen=True, slots=True)
class _SelectedSpan:
    text: str
    char_start: int
    char_end: int
    derivation: str
    semantic_score: float | None
    semantic_span_aligned: bool


class GroundingService:
    """Apply measured thresholds without asking the generator to self-grade."""

    def __init__(
        self,
        config: ChatConfig,
        embedder: BaseEmbeddingProvider | None = None,
    ) -> None:
        self._config = config
        self._embedder = embedder

    def assess(
        self,
        question: str,
        chunks: list[ContextChunk],
        *,
        rerank_status: str | None = None,
    ) -> EvidenceDecision:
        if _rerank_applied(chunks, rerank_status=rerank_status):
            return self.assess_candidate_wise(
                question,
                chunks,
                rerank_status=rerank_status,
            )
        return self.assess_without_reranker(question, chunks, rerank_status=rerank_status)

    def assess_without_reranker(
        self,
        question: str,
        chunks: list[ContextChunk],
        *,
        rerank_status: str | None = None,
    ) -> EvidenceDecision:
        """Admit independently supported candidates when no reranker was applied."""
        del rerank_status
        if not chunks:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS,
                evidence_score_method="whole_chunk_cosine",
                evidence_calibration_id="whole_chunk_cosine:v1",
                grounding_path="no_reranker",
            )
        assessments: list[CandidateEvidenceAssessment] = []
        units: list[EvidenceUnit] = []
        for rank, chunk in enumerate(chunks, start=1):
            assessment, unit = self._assess_unreranked_candidate(question, chunk, rank=rank)
            assessments.append(assessment)
            if unit is not None:
                units.append(unit)
        return _admission_decision(
            assessments,
            units,
            grounding_path="no_reranker",
            evidence_score_method="whole_chunk_cosine",
            evidence_calibration_id="whole_chunk_cosine:v1",
        )

    def assess_candidate_wise(
        self,
        question: str,
        chunks: list[ContextChunk],
        *,
        rerank_status: str | None = None,
    ) -> EvidenceDecision:
        """Evaluate every reranked candidate and admit all independently supported spans."""
        if not chunks:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS,
                evidence_score_method="reranker_relevance",
                evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
                grounding_path="candidate_wise",
            )
        if not _rerank_applied(chunks, rerank_status=rerank_status):
            return self.assess_without_reranker(question, chunks, rerank_status=rerank_status)

        assessments: list[CandidateEvidenceAssessment] = []
        units: list[EvidenceUnit] = []
        for rank, chunk in enumerate(chunks, start=1):
            assessment, unit = self._assess_reranked_candidate(
                question,
                chunk,
                rank=rank,
                rerank_status=rerank_status,
            )
            assessments.append(assessment)
            if unit is not None:
                units.append(unit)

        return _admission_decision(
            assessments,
            units,
            grounding_path="candidate_wise",
            evidence_score_method="reranker_relevance",
            evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
        )

    def merge_monotonic_admissions(
        self,
        previous: EvidenceDecision,
        current: EvidenceDecision,
    ) -> EvidenceDecision:
        """Keep previously admitted units when a later reassessment would drop them."""
        del self
        if not previous.candidate_assessments:
            return current
        previous_assessments = {item.chunk_id: item for item in previous.candidate_assessments}
        previous_units = {unit.chunk_id: unit for unit in previous.admitted_units}
        current_units = {unit.chunk_id: unit for unit in current.admitted_units}
        assessments: list[CandidateEvidenceAssessment] = []
        units: list[EvidenceUnit] = []
        for assessment in current.candidate_assessments:
            prior = previous_assessments.get(assessment.chunk_id)
            if prior is not None and prior.passed:
                assessments.append(prior)
                unit = previous_units.get(assessment.chunk_id)
            else:
                assessments.append(assessment)
                unit = current_units.get(assessment.chunk_id)
            if unit is not None:
                units.append(unit)
        return replace(
            _admission_decision(
                assessments,
                units,
                grounding_path=current.grounding_path,
                evidence_score_method=current.evidence_score_method,
                evidence_calibration_id=current.evidence_calibration_id,
            ),
            grounding_path=current.grounding_path,
        )

    def _assess_reranked_candidate(
        self,
        question: str,
        chunk: ContextChunk,
        *,
        rank: int,
        rerank_status: str | None,
    ) -> tuple[CandidateEvidenceAssessment, EvidenceUnit | None]:
        reranker_score = _reranker_relevance(chunk, rerank_status=rerank_status)
        calibration_status = _reranker_calibration_status(chunk)
        provided_calibration = chunk.evidence_calibration_id
        provenance_missing = not chunk.query_variants
        variants = chunk.query_variants or (
            QueryVariant(
                variant_id="original",
                kind=QueryVariantKind.ORIGINAL,
                language=detect_language(question).primary_language or "und",
                text=question,
            ),
        )
        span = _select_evidence_span(
            chunk,
            variants,
            max_chars=self._config.context_char_budget,
        )
        original = next(
            (variant for variant in variants if variant.kind is QueryVariantKind.ORIGINAL),
            None,
        )
        original_text = original.text if original is not None else question
        original_coverage = (
            _evidence_coverage(original_text, span.text) if span is not None else 0.0
        )
        translated_lexical_ids = {
            item.query_variant_id
            for item in chunk.branch_contributions
            if item.family.startswith("translated_lexical")
        }
        translated_coverages = {
            variant.variant_id: _evidence_coverage(variant.text, span.text)
            for variant in variants
            if variant.variant_id in translated_lexical_ids and span is not None
        }
        translated_dense_scores = {
            item.query_variant_id: item.raw_score
            for item in chunk.branch_contributions
            if item.family.startswith("translated_dense")
        }

        corroboration: str | None = None
        corroborating_variant_id = original.variant_id if original is not None else "original"
        semantic_score = span.semantic_score if span is not None else None
        if span is not None and span.semantic_span_aligned and semantic_score is not None:
            if semantic_score >= self._config.minimum_semantic_evidence_score:
                corroboration = "original_semantic"
            elif (
                not _same_language(original_text, span.text)
                and semantic_score >= self._config.cross_language_semantic_evidence_score_threshold
            ):
                corroboration = "cross_language_semantic"
        if (
            corroboration is None
            and span is not None
            and _lexical_support(
                original_text,
                span.text,
                minimum_coverage=self._config.lexical_corroboration_coverage,
            )
        ):
            corroboration = "original_lexical"
        if corroboration is None and span is not None:
            for variant in variants:
                if variant.variant_id not in translated_lexical_ids:
                    continue
                if _lexical_support(
                    variant.text,
                    span.text,
                    minimum_coverage=self._config.lexical_corroboration_coverage,
                ):
                    corroboration = "translated_lexical"
                    corroborating_variant_id = variant.variant_id
                    break

        terminal_reason = "admitted"
        if reranker_score is None:
            terminal_reason = "missing_reranker_score"
        elif calibration_status == "mismatch":
            terminal_reason = "calibration_mismatch"
        elif span is None:
            terminal_reason = "no_safe_evidence_span"
        elif reranker_score < self._config.minimum_reranker_evidence_score:
            terminal_reason = "below_reranker_threshold"
        elif corroboration is None and _balanced_high_confidence_admission(
            self._config,
            reranker_score=reranker_score,
            calibration_status=calibration_status,
            span=span,
        ):
            corroboration = "high_confidence_reranker"
        elif corroboration is None:
            terminal_reason = "no_aligned_independent_signal"
        passed = terminal_reason == "admitted"
        unit = (
            _evidence_unit(
                chunk,
                span,
                query_variant_id=corroborating_variant_id,
                corroboration_method=corroboration,
            )
            if passed and span is not None and corroboration is not None
            else None
        )
        assessment = CandidateEvidenceAssessment(
            candidate_rank=rank,
            chunk_id=chunk.chunk_id,
            reranker_score=reranker_score,
            reranker_threshold=self._config.minimum_reranker_evidence_score,
            reranker_calibration_id=provided_calibration,
            calibration_status=calibration_status,
            query_variant_ids=tuple(variant.variant_id for variant in variants),
            branch_contributions=chunk.branch_contributions,
            span_derivation=span.derivation if span is not None else None,
            evidence_char_start=span.char_start if span is not None else None,
            evidence_char_end=span.char_end if span is not None else None,
            evidence_span_hash=content_hash(span.text) if span is not None else None,
            evidence_unit_id=unit.evidence_unit_id if unit is not None else None,
            original_semantic_score=semantic_score,
            semantic_span_aligned=span.semantic_span_aligned if span is not None else False,
            original_lexical_coverage=original_coverage,
            translated_lexical_coverage=translated_coverages,
            translated_dense_scores=translated_dense_scores,
            corroboration_method=corroboration,
            query_variant_provenance_missing=provenance_missing,
            passed=passed,
            terminal_reason=terminal_reason,
        )
        return assessment, unit

    def _assess_unreranked_candidate(
        self,
        question: str,
        chunk: ContextChunk,
        *,
        rank: int,
    ) -> tuple[CandidateEvidenceAssessment, EvidenceUnit | None]:
        provenance_missing = not chunk.query_variants
        variants = chunk.query_variants or (
            QueryVariant(
                variant_id="original",
                kind=QueryVariantKind.ORIGINAL,
                language=detect_language(question).primary_language or "und",
                text=question,
            ),
        )
        original = next(
            (variant for variant in variants if variant.kind is QueryVariantKind.ORIGINAL),
            None,
        )
        original_text = original.text if original is not None else question
        span = (
            _SelectedSpan(
                text=chunk.content,
                char_start=0,
                char_end=len(chunk.content),
                derivation="complete_chunk",
                semantic_score=chunk.semantic_score,
                semantic_span_aligned=True,
            )
            if chunk.content
            else None
        )
        original_coverage = (
            _evidence_coverage(original_text, span.text) if span is not None else 0.0
        )
        semantic_score = chunk.semantic_score
        corroboration: str | None = None
        corroborating_variant_id = original.variant_id if original is not None else "original"
        terminal_reason = "admitted"
        if span is None:
            terminal_reason = "no_safe_evidence_span"
        elif semantic_score is None:
            terminal_reason = "missing_semantic_score"
        elif semantic_score >= self._config.minimum_semantic_evidence_score:
            corroboration = "original_semantic"
        elif (
            not _same_language(original_text, span.text)
            and semantic_score >= self._config.cross_language_semantic_evidence_score_threshold
        ):
            corroboration = "cross_language_semantic"
        elif semantic_score >= self._config.lexical_corroboration_floor_score and _lexical_support(
            original_text,
            span.text,
            minimum_coverage=self._config.lexical_corroboration_coverage,
        ):
            corroboration = "original_lexical"
        else:
            terminal_reason = "no_aligned_independent_signal"
        passed = terminal_reason == "admitted" and corroboration is not None
        if not passed and terminal_reason == "admitted":
            terminal_reason = "no_aligned_independent_signal"
        unit = (
            _evidence_unit(
                chunk,
                span,
                query_variant_id=corroborating_variant_id,
                corroboration_method=corroboration,
            )
            if passed and span is not None and corroboration is not None
            else None
        )
        assessment = CandidateEvidenceAssessment(
            candidate_rank=rank,
            chunk_id=chunk.chunk_id,
            reranker_score=None,
            reranker_threshold=self._config.minimum_reranker_evidence_score,
            reranker_calibration_id=None,
            calibration_status="not_applicable",
            query_variant_ids=tuple(variant.variant_id for variant in variants),
            branch_contributions=chunk.branch_contributions,
            span_derivation=span.derivation if span is not None else None,
            evidence_char_start=span.char_start if span is not None else None,
            evidence_char_end=span.char_end if span is not None else None,
            evidence_span_hash=content_hash(span.text) if span is not None else None,
            evidence_unit_id=unit.evidence_unit_id if unit is not None else None,
            original_semantic_score=semantic_score,
            semantic_span_aligned=span.semantic_span_aligned if span is not None else False,
            original_lexical_coverage=original_coverage,
            translated_lexical_coverage={},
            translated_dense_scores={},
            corroboration_method=corroboration,
            query_variant_provenance_missing=provenance_missing,
            passed=passed,
            terminal_reason=terminal_reason,
        )
        return assessment, unit

    def passage_rescue_chunk_ids(
        self,
        chunks: list[ContextChunk],
        assessments: tuple[CandidateEvidenceAssessment, ...],
    ) -> list[uuid.UUID]:
        """Select high-confidence reranker misses that may be whole-chunk diluted."""
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        ranked: list[tuple[float, int, uuid.UUID]] = []
        for assessment in assessments:
            chunk = by_id.get(assessment.chunk_id)
            if chunk is None or chunk.passage_semantic_score is not None:
                continue
            if assessment.passed or assessment.terminal_reason != "no_aligned_independent_signal":
                continue
            if assessment.calibration_status != "matched" or assessment.span_derivation is None:
                continue
            if not _high_confidence_reranker(assessment.reranker_score, self._config):
                continue
            if not _assessment_near_miss(self._config, assessment):
                continue
            ranked.append(
                (
                    assessment.reranker_score or 0.0,
                    -assessment.candidate_rank,
                    assessment.chunk_id,
                )
            )
        ranked.sort(reverse=True)
        return [chunk_id for _, _, chunk_id in ranked[:_PASSAGE_RESCUE_MAX_CANDIDATES]]

    async def apply_passage_rescue(
        self,
        question: str,
        chunks: list[ContextChunk],
        chunk_ids: list[uuid.UUID],
        *,
        window_tokens: int,
        overlap_tokens: int,
        min_tokens: int,
    ) -> tuple[list[ContextChunk], str]:
        """Score bounded passages for rescue candidates and attach the winning span."""
        if not chunk_ids:
            return chunks, "not_needed"
        if not _usable_embedder(self._embedder):
            return chunks, "unavailable"
        embedder = self._embedder
        if embedder is None:
            return chunks, "unavailable"
        selected = {
            chunk.chunk_id: chunk.content for chunk in chunks if chunk.chunk_id in chunk_ids
        }
        try:
            query_embedded = await embedder.embed_texts(
                [question],
                purpose=EmbeddingPurpose.QUERY,
            )
            best = await score_best_passages(
                embedder=embedder,
                query_vector=query_embedded.vectors[0],
                texts=selected,
                window_tokens=window_tokens,
                overlap_tokens=overlap_tokens,
                minimum_tokens=min_tokens,
            )
        except ProviderError:
            return chunks, "unavailable"
        updated: list[ContextChunk] = []
        attached = 0
        for chunk in chunks:
            winner = best.get(chunk.chunk_id)
            if winner is None:
                updated.append(chunk)
                continue
            score, passage = winner
            if chunk.semantic_score is not None and score <= chunk.semantic_score:
                updated.append(chunk)
                continue
            attached += 1
            updated.append(
                replace(
                    chunk,
                    passage_semantic_score=score,
                    passage_char_start=passage.char_start,
                    passage_char_end=passage.char_end,
                    passage_score_method="bounded_token_max_v1",
                    metadata={
                        **chunk.metadata,
                        "passage_semantic_score": score,
                        "passage_char_start": passage.char_start,
                        "passage_char_end": passage.char_end,
                        "passage_score_method": "bounded_token_max_v1",
                        "passage_score_status": "rescued",
                    },
                )
            )
        return updated, "applied" if attached else "not_needed"

    def blocks_generation(self, decision: EvidenceDecision) -> bool:
        """Refuse before the LLM only when policy says the gate may block.

        Empty retrieval always blocks. Observe mode still records the score
        decision but does not treat cosine failure as a generation veto.
        """
        if decision.reason is InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS:
            return True
        if self._config.evidence_gate_mode is EvidenceGateMode.OBSERVE:
            return False
        return not decision.sufficient

    def diagnostics(
        self,
        decision: EvidenceDecision,
        *,
        blocked_generation: bool,
        generation_ran: bool,
    ) -> dict[str, Any]:
        reason = decision.reason.value if decision.reason is not None else None
        admitted_units = list(decision.admitted_units)
        stage = failure_stage_for(decision)
        return {
            "mode": self._config.evidence_gate_mode.value,
            "sufficient": decision.sufficient,
            "reason": reason,
            "failure_stage": stage,
            "blocked_generation": blocked_generation,
            "generation_ran": generation_ran,
            "evidence_score": decision.best_score,
            "evidence_score_method": decision.evidence_score_method,
            "evidence_calibration_id": decision.evidence_calibration_id,
            "winning_chunk_id": (
                str(decision.winning_chunk_id) if decision.winning_chunk_id is not None else None
            ),
            "winning_semantic_score": decision.winning_semantic_score,
            "winning_rank_score": decision.winning_rank_score,
            "query_token_coverage": decision.query_token_coverage,
            "lexically_corroborated": decision.lexically_corroborated,
            "semantic_threshold": self._config.minimum_semantic_evidence_score,
            "lexical_floor": self._config.lexical_corroboration_floor_score,
            "cross_language_semantic_threshold": (
                self._config.cross_language_semantic_evidence_score_threshold
            ),
            "reranker_threshold": self._config.minimum_reranker_evidence_score,
            "high_confidence_reranker_threshold": (
                self._config.high_confidence_reranker_evidence_score
            ),
            "grounding_mode": self._config.grounding_mode.value,
            "high_confidence_band_enabled": self._config.high_confidence_band_enabled,
            "winning_char_start": decision.evidence_char_start,
            "winning_char_end": decision.evidence_char_end,
            "winning_evidence_unit_id": (
                admitted_units[0].evidence_unit_id if admitted_units else None
            ),
            "winning_span_hash": (admitted_units[0].evidence_span_hash if admitted_units else None),
            "passage_rescue": {
                "status": decision.passage_rescue_status,
                "candidate_count": decision.passage_rescue_candidate_count,
            },
            "context_selection": {
                "reason": reason if stage == "context_selection" else None,
                "observe_context": decision.observe_context,
            },
            "candidate_wise": {
                "path": decision.grounding_path,
                "assessed_count": len(decision.candidate_assessments),
                "admitted_count": len(decision.admitted_units),
                "alerts": {
                    "unknown_calibration_count": sum(
                        item.calibration_status == "mismatch"
                        for item in decision.candidate_assessments
                    ),
                    "failed_span_derivation_count": sum(
                        item.terminal_reason == "no_safe_evidence_span"
                        for item in decision.candidate_assessments
                    ),
                    "missing_provenance_count": sum(
                        item.query_variant_provenance_missing
                        for item in decision.candidate_assessments
                    ),
                },
                "assessments": [
                    _assessment_diagnostic(item) for item in decision.candidate_assessments
                ],
            },
        }

    async def map_claims(
        self,
        answer: str,
        chunks: list[ContextChunk],
        *,
        require_citations: bool = True,
    ) -> GroundingResult:
        drafts: list[_ClaimDraft] = []
        semantic_pairs: list[tuple[str, str]] = []
        segments = _answer_segments(answer)
        for index, raw_segment in enumerate(segments, start=1):
            segment = raw_segment.strip()
            if not segment:
                continue
            citation_indexes = [int(value) for value in _CITATION_PATTERN.findall(segment)]
            claim_text = _CITATION_PATTERN.sub("", segment).strip()
            if (
                not claim_text
                or _is_structural_segment(claim_text)
                or _is_short_stance_segment(claim_text)
                or _is_insufficiency_statement(claim_text)
            ):
                continue
            evidence_chunks = [
                (citation_index, chunks[citation_index - 1])
                for citation_index in dict.fromkeys(citation_indexes)
                if 1 <= citation_index <= len(chunks)
            ]
            has_valid_citation = bool(evidence_chunks)
            if not evidence_chunks and not require_citations:
                best = _best_evidence(claim_text, chunks)
                if best is not None:
                    evidence_chunks = [best]
            drafts.append(
                _ClaimDraft(
                    index=index,
                    text=claim_text,
                    evidence_chunks=evidence_chunks,
                    has_valid_citation=has_valid_citation,
                )
            )
            if evidence_chunks:
                semantic_pairs.extend((claim_text, chunk.content) for _, chunk in evidence_chunks)
        similarities = await self._claim_similarities(semantic_pairs)

        claims: list[AnswerClaim] = []
        supported = 0
        unverified = 0
        cited = 0
        for draft in drafts:
            evidence_texts = [chunk.content for _, chunk in draft.evidence_chunks]
            if not draft.evidence_chunks:
                verification = ClaimVerification.UNSUPPORTED
            else:
                neighbor = _nearest_matching_cited_calculation(segments, draft.index - 1)
                adjacent_texts = (segments[neighbor],) if neighbor is not None else ()
                derived = _derived_calculation_verification(
                    draft.text,
                    evidence_texts,
                    adjacent_texts=adjacent_texts,
                )
                if derived is not None:
                    verification = derived
                else:
                    uses_lexical = _uses_lexical_verification(draft.text, evidence_texts)
                    lexical = (
                        self._lexical_verification(draft.text, evidence_texts)
                        if uses_lexical
                        else None
                    )
                    scores = [
                        similarities.get((draft.text, chunk.content))
                        for _, chunk in draft.evidence_chunks
                    ]
                    numeric = [value for value in scores if value is not None]
                    score = max(numeric) if numeric else None
                    semantic = (
                        self._cross_language_verification(score)
                        if not uses_lexical or _usable_embedder(self._embedder)
                        else None
                    )
                    verification = _combine_claim_verification(lexical, semantic)
            claim_grounded = verification is ClaimVerification.SUPPORTED
            supported += int(claim_grounded)
            unverified += int(verification is ClaimVerification.UNVERIFIED)
            cited += int(draft.has_valid_citation)
            claims.append(
                AnswerClaim(
                    claim_id=f"claim-{draft.index}",
                    text=draft.text,
                    grounded=claim_grounded,
                    verification=verification,
                    evidence=[
                        _evidence_snapshot(citation_index, chunk, self._config)
                        for citation_index, chunk in draft.evidence_chunks
                    ],
                )
            )
        total = len(claims)
        if not claims:
            # All segments were polarity-only, headings, or other non-factual
            # content.  grounded=None distinguishes this from an ungrounded
            # factual answer (grounded=False).  The caller (chat_service) only
            # uses this when generation ran on admitted evidence.
            return GroundingResult(
                claims=[],
                grounded=None,
                citation_coverage=0.0,
                unverified_claim_rate=0.0,
                claims_status="no_verifiable_claims",
            )
        return GroundingResult(
            claims=[claim.model_dump(mode="json") for claim in claims],
            grounded=bool(claims) and supported == total,
            citation_coverage=(cited / total) if total else 0.0,
            unverified_claim_rate=(unverified / total) if total else 0.0,
        )

    def _lexical_verification(self, text: str, evidence_texts: list[str]) -> ClaimVerification:
        claim_tokens = _significant_tokens(text)
        evidence_tokens: set[str] = set()
        for evidence in evidence_texts:
            evidence_tokens.update(_significant_tokens(evidence))
        shared_tokens = claim_tokens & evidence_tokens
        coverage = _coverage(claim_tokens, evidence_tokens)
        if coverage >= self._config.minimum_claim_token_coverage:
            return ClaimVerification.SUPPORTED
        if not shared_tokens:
            return ClaimVerification.UNVERIFIED
        return ClaimVerification.UNSUPPORTED

    def _cross_language_verification(self, score: float | None) -> ClaimVerification:
        if score is None:
            return ClaimVerification.UNVERIFIED
        if score >= self._config.minimum_claim_semantic_score:
            return ClaimVerification.SUPPORTED
        if score < self._config.claim_semantic_reject_floor:
            return ClaimVerification.UNSUPPORTED
        return ClaimVerification.UNVERIFIED

    async def _claim_similarities(
        self,
        pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], float | None]:
        unique_pairs = list(dict.fromkeys(pairs))
        missing = dict.fromkeys(unique_pairs)
        if not unique_pairs or not _usable_embedder(self._embedder):
            return missing
        embedder = self._embedder
        if embedder is None:
            return missing
        claim_texts = list(dict.fromkeys(pair[0] for pair in unique_pairs))
        evidence_texts = list(dict.fromkeys(pair[1] for pair in unique_pairs))
        try:
            claim_embedded = await embedder.embed_texts(
                claim_texts,
                purpose=EmbeddingPurpose.QUERY,
            )
            evidence_embedded = await embedder.embed_texts(
                evidence_texts,
                purpose=EmbeddingPurpose.DOCUMENT,
            )
        except ProviderError:
            return missing
        by_claim = dict(zip(claim_texts, claim_embedded.vectors, strict=True))
        by_evidence = dict(zip(evidence_texts, evidence_embedded.vectors, strict=True))
        return {
            pair: cosine_similarity(by_claim[pair[0]], by_evidence[pair[1]])
            for pair in unique_pairs
        }


def failure_stage_for(decision: EvidenceDecision) -> str | None:
    """Map an insufficient decision onto admission, context selection, or retrieval."""
    if decision.sufficient or decision.reason is None:
        return None
    if decision.reason is InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS:
        return "retrieval"
    if decision.reason in _CONTEXT_SELECTION_REASONS:
        return "context_selection"
    return "admission"


def _admission_decision(
    assessments: list[CandidateEvidenceAssessment],
    units: list[EvidenceUnit],
    *,
    grounding_path: str,
    evidence_score_method: str,
    evidence_calibration_id: str,
) -> EvidenceDecision:
    if not assessments:
        return EvidenceDecision(
            sufficient=False,
            reason=InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS,
            evidence_score_method=evidence_score_method,
            evidence_calibration_id=evidence_calibration_id,
            grounding_path=grounding_path,
        )
    winner = _winning_assessment(assessments)
    winning_unit = next(
        (unit for unit in units if unit.chunk_id == winner.chunk_id),
        None,
    )
    best_score = (
        winner.reranker_score
        if grounding_path == "candidate_wise"
        else winner.original_semantic_score
    )
    return EvidenceDecision(
        sufficient=bool(units),
        reason=(None if units else InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD),
        query_token_coverage=max(
            [
                winner.original_lexical_coverage,
                *winner.translated_lexical_coverage.values(),
            ]
        ),
        best_score=best_score,
        lexically_corroborated=winner.corroboration_method
        in {
            "original_lexical",
            "translated_lexical",
        },
        winning_chunk_id=winner.chunk_id,
        evidence_score_method=evidence_score_method,
        evidence_calibration_id=evidence_calibration_id,
        evidence_char_start=(
            winning_unit.evidence_char_start if winning_unit is not None else None
        ),
        evidence_char_end=(winning_unit.evidence_char_end if winning_unit is not None else None),
        winning_semantic_score=winner.original_semantic_score,
        winning_rank_score=(winning_unit.rank_score if winning_unit is not None else None),
        admitted_units=tuple(units),
        candidate_assessments=tuple(assessments),
        grounding_path=grounding_path,
    )


def _winning_assessment(
    assessments: list[CandidateEvidenceAssessment],
) -> CandidateEvidenceAssessment:
    strict = next(
        (
            item
            for item in assessments
            if item.passed and is_strict_corroboration(item.corroboration_method)
        ),
        None,
    )
    if strict is not None:
        return strict
    return next((item for item in assessments if item.passed), assessments[0])


def is_strict_corroboration(method: str | None) -> bool:
    return method in _STRICT_CORROBORATION_METHODS


def _admission_kind(corroboration: str | None, passed: bool) -> str | None:
    if not passed or corroboration is None:
        return None
    if corroboration == "high_confidence_reranker":
        return "balanced_high_confidence"
    return "strict"


def _select_evidence_span(
    chunk: ContextChunk,
    variants: tuple[QueryVariant, ...],
    *,
    max_chars: int,
) -> _SelectedSpan | None:
    """Choose a scored passage, complete chunk, or deterministic match-local span."""
    passage_start = chunk.passage_char_start
    passage_end = chunk.passage_char_end
    if (
        chunk.passage_semantic_score is not None
        and passage_start is not None
        and passage_end is not None
        and 0 <= passage_start < passage_end <= len(chunk.content)
    ):
        return _SelectedSpan(
            text=chunk.content[passage_start:passage_end],
            char_start=passage_start,
            char_end=passage_end,
            derivation="scored_passage",
            semantic_score=chunk.passage_semantic_score,
            semantic_span_aligned=True,
        )
    if len(chunk.content) <= max_chars:
        return _SelectedSpan(
            text=chunk.content,
            char_start=0,
            char_end=len(chunk.content),
            derivation="complete_chunk",
            semantic_score=chunk.semantic_score,
            semantic_span_aligned=True,
        )
    local = _bounded_match_span(
        chunk.content,
        tuple(variant.text for variant in variants),
        max_chars=max_chars,
    )
    if local is None:
        return None
    start, end = local
    return _SelectedSpan(
        text=chunk.content[start:end],
        char_start=start,
        char_end=end,
        derivation="match_local_sentence_v1",
        semantic_score=None,
        semantic_span_aligned=False,
    )


def _bounded_match_span(
    content: str,
    query_texts: tuple[str, ...],
    *,
    max_chars: int,
) -> tuple[int, int] | None:
    token_sets = [tokens for text in query_texts if (tokens := _significant_tokens(text))]
    if not content or not token_sets:
        return None
    segments: list[tuple[int, int]] = []
    start = 0
    for boundary in _SPAN_BOUNDARY_PATTERN.finditer(content):
        end = boundary.start()
        if content[start:end].strip():
            segments.append((start, end))
        start = boundary.end()
    if content[start:].strip():
        segments.append((start, len(content)))
    if not segments:
        segments = [(0, len(content))]

    scored: list[tuple[float, int, int, int, int]] = []
    for index, (segment_start, segment_end) in enumerate(segments):
        actual = _significant_tokens(content[segment_start:segment_end])
        best_coverage = max(_coverage(tokens, actual) for tokens in token_sets)
        shared = max(len(tokens & actual) for tokens in token_sets)
        if shared:
            scored.append(
                (best_coverage, shared, -segment_start, index, segment_end - segment_start)
            )
    if not scored:
        return None
    _, _, _, winner_index, winner_length = max(scored)
    span_start, span_end = segments[winner_index]
    if winner_length > max_chars:
        return _bounded_token_window(
            content,
            span_start,
            span_end,
            token_sets,
            max_chars=max_chars,
        )

    left = winner_index - 1
    right = winner_index + 1
    while True:
        changed = False
        if left >= 0 and span_end - segments[left][0] <= max_chars:
            span_start = segments[left][0]
            left -= 1
            changed = True
        if right < len(segments) and segments[right][1] - span_start <= max_chars:
            span_end = segments[right][1]
            right += 1
            changed = True
        if not changed:
            break
    return span_start, span_end


def _bounded_token_window(
    content: str,
    segment_start: int,
    segment_end: int,
    token_sets: list[set[str]],
    *,
    max_chars: int,
) -> tuple[int, int] | None:
    folded = content.casefold()
    shared_tokens = sorted(
        set().union(*token_sets) & _significant_tokens(content[segment_start:segment_end]),
        key=lambda value: (-len(value), value),
    )
    anchor = next(
        (
            position
            for token in shared_tokens
            if (position := folded.find(token.casefold(), segment_start, segment_end)) >= 0
        ),
        None,
    )
    if anchor is None:
        return None
    start = max(segment_start, anchor - max_chars // 2)
    end = min(segment_end, start + max_chars)
    start = max(segment_start, end - max_chars)
    if start > segment_start:
        whitespace = regex.search(r"\s", content[start:end])
        if whitespace is not None:
            start += whitespace.end()
    if end < segment_end:
        trailing = list(regex.finditer(r"\s", content[start:end]))
        if trailing:
            end = start + trailing[-1].start()
    return (start, end) if start < end else None


def _evidence_unit(
    chunk: ContextChunk,
    span: _SelectedSpan,
    *,
    query_variant_id: str,
    corroboration_method: str,
) -> EvidenceUnit:
    span_hash = content_hash(span.text)
    unit_id = content_hash(
        f"evidence-unit:v1:{chunk.chunk_id}:{span.char_start}:{span.char_end}:{span_hash}"
    )
    document_start = (
        chunk.char_start + span.char_start if chunk.char_start is not None else span.char_start
    )
    document_end = (
        chunk.char_start + span.char_end if chunk.char_start is not None else span.char_end
    )
    metadata = {
        **chunk.metadata,
        "evidence_unit_id": unit_id,
        "evidence_span_hash": span_hash,
        "evidence_source_chunk_hash": chunk.chunk_hash,
        "evidence_chunk_char_start": span.char_start,
        "evidence_chunk_char_end": span.char_end,
        "evidence_span_derivation": span.derivation,
        "evidence_query_variant_id": query_variant_id,
        "evidence_corroboration_method": corroboration_method,
    }
    return EvidenceUnit(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        content=span.text,
        score=chunk.score,
        filename=chunk.filename,
        chunk_hash=span_hash,
        semantic_score=span.semantic_score if span.semantic_span_aligned else None,
        rank_score=chunk.rank_score,
        rerank_relevance_score=chunk.rerank_relevance_score,
        evidence_relevance_score=chunk.evidence_relevance_score,
        evidence_score_method=chunk.evidence_score_method,
        evidence_calibration_id=chunk.evidence_calibration_id,
        passage_semantic_score=(
            span.semantic_score if span.derivation == "scored_passage" else None
        ),
        passage_char_start=0 if span.derivation == "scored_passage" else None,
        passage_char_end=len(span.text) if span.derivation == "scored_passage" else None,
        passage_score_method=(
            chunk.passage_score_method if span.derivation == "scored_passage" else None
        ),
        page_number=chunk.page_number,
        char_start=document_start,
        char_end=document_end,
        query_variants=chunk.query_variants,
        branch_contributions=chunk.branch_contributions,
        metadata=metadata,
        evidence_unit_id=unit_id,
        source_chunk_hash=chunk.chunk_hash,
        evidence_span_hash=span_hash,
        evidence_char_start=span.char_start,
        evidence_char_end=span.char_end,
        span_derivation=span.derivation,
        query_variant_id=query_variant_id,
        corroboration_method=corroboration_method,
    )


def _evidence_coverage(query: str, evidence: str) -> float:
    expected = _significant_tokens(query)
    if not expected:
        return 0.0
    return _coverage(expected, _significant_tokens(evidence))


def _lexical_support(query: str, evidence: str, *, minimum_coverage: float) -> bool:
    expected = _significant_tokens(query)
    if not expected:
        return False
    actual = _significant_tokens(evidence)
    shared = expected & actual
    minimum_shared = 1 if len(expected) == 1 else 2
    return len(shared) >= minimum_shared and _coverage(expected, actual) >= minimum_coverage


def _high_confidence_reranker(score: float | None, config: ChatConfig) -> bool:
    return score is not None and score >= config.high_confidence_reranker_evidence_score


def _near_miss_value(value: float, threshold: float) -> bool:
    return threshold - _CORROBORATION_NEAR_MISS_MARGIN <= value < threshold


def _assessment_near_miss(config: ChatConfig, assessment: CandidateEvidenceAssessment) -> bool:
    if assessment.semantic_span_aligned and assessment.original_semantic_score is not None:
        semantic = assessment.original_semantic_score
        if _near_miss_value(semantic, config.minimum_semantic_evidence_score):
            return True
        if _near_miss_value(semantic, config.cross_language_semantic_evidence_score_threshold):
            return True
    coverages = [
        assessment.original_lexical_coverage,
        *assessment.translated_lexical_coverage.values(),
    ]
    return any(
        _near_miss_value(coverage, config.lexical_corroboration_coverage) for coverage in coverages
    )


def _balanced_high_confidence_admission(
    config: ChatConfig,
    *,
    reranker_score: float | None,
    calibration_status: str,
    span: _SelectedSpan | None,
) -> bool:
    return (
        config.grounding_mode is GroundingMode.BALANCED
        and config.high_confidence_band_enabled
        and _high_confidence_reranker(reranker_score, config)
        and calibration_status == "matched"
        and span is not None
    )


def _assessment_diagnostic(assessment: CandidateEvidenceAssessment) -> dict[str, Any]:
    return {
        "candidate_rank": assessment.candidate_rank,
        "chunk_id": str(assessment.chunk_id),
        "reranker_score": assessment.reranker_score,
        "reranker_threshold": assessment.reranker_threshold,
        "reranker_calibration_id": assessment.reranker_calibration_id,
        "calibration_status": assessment.calibration_status,
        "query_variant_ids": list(assessment.query_variant_ids),
        "branch_contributions": [
            {
                "branch_id": item.branch_id,
                "family": item.family,
                "query_variant_id": item.query_variant_id,
                "target_language": item.target_language,
                "rank": item.rank,
                "raw_score": item.raw_score,
                "score_type": item.score_type.value,
            }
            for item in assessment.branch_contributions
        ],
        "span_derivation": assessment.span_derivation,
        "evidence_char_start": assessment.evidence_char_start,
        "evidence_char_end": assessment.evidence_char_end,
        "evidence_span_hash": assessment.evidence_span_hash,
        "evidence_unit_id": assessment.evidence_unit_id,
        "original_semantic_score": assessment.original_semantic_score,
        "semantic_span_aligned": assessment.semantic_span_aligned,
        "original_lexical_coverage": assessment.original_lexical_coverage,
        "translated_lexical_coverage": dict(assessment.translated_lexical_coverage),
        "translated_dense_scores": dict(assessment.translated_dense_scores),
        "corroboration_method": assessment.corroboration_method,
        "admission_kind": _admission_kind(assessment.corroboration_method, assessment.passed),
        "query_variant_provenance_missing": assessment.query_variant_provenance_missing,
        "passed": assessment.passed,
        "terminal_reason": assessment.terminal_reason,
    }


def _answer_segments(answer: str) -> list[str]:
    """Split claims and safely inherit nearby citations.

    Inheritance only supplies candidate evidence.  Every sentence is still
    verified independently by ``map_claims`` before it can be grounded.
    """
    segments: list[str] = []
    for paragraph in regex.split(r"\n\s*\n", answer):
        paragraph_segments: list[str] = []
        for raw_segment in _SEGMENT_PATTERN.split(paragraph):
            segment = raw_segment.strip()
            if not segment:
                continue
            leading = _LEADING_CITATIONS_PATTERN.match(segment)
            if leading is not None and paragraph_segments:
                paragraph_segments[-1] = f"{paragraph_segments[-1]} {leading.group(1).strip()}"
                segment = leading.group(2).strip()
            if segment:
                paragraph_segments.append(segment)
        if paragraph_segments:
            final_citations = _CITATION_PATTERN.findall(paragraph_segments[-1])
            if final_citations:
                inherited = " ".join(f"[{value}]" for value in dict.fromkeys(final_citations))
                paragraph_segments = [
                    segment if _CITATION_PATTERN.search(segment) else f"{segment} {inherited}"
                    for segment in paragraph_segments
                ]
            segments.extend(paragraph_segments)
    return _inherit_bounded_block_citations(segments)


def _inherit_bounded_block_citations(segments: list[str]) -> list[str]:
    """Attach shared citations around one isolated factual Markdown block.

    Generators often put a calculation in its own displayed Markdown block
    while citing the factual sentence immediately before and after it. This
    accepts that bounded pattern: the nearest factual neighbours must both be
    cited and share a citation. Uncited Markdown list items in between are
    treated as one block rather than a hard boundary. At most one heading or
    list preamble may appear between them, and another non-list factual claim
    is a hard boundary.

    A derived conclusion that restates the result of an adjacent cited
    calculation may inherit that calculation's citations from one side.
    Restating only the base amount is not enough.
    """
    inherited = list(segments)
    for index, segment in enumerate(segments):
        if _CITATION_PATTERN.search(segment) or _is_non_factual_segment(segment):
            continue
        before = _nearest_cited_factual_segment(segments, index, direction=-1)
        after = _nearest_cited_factual_segment(segments, index, direction=1)
        shared: set[str] = set()
        if before is not None and after is not None:
            shared = set(_CITATION_PATTERN.findall(segments[before])) & set(
                _CITATION_PATTERN.findall(segments[after])
            )
        if not shared:
            neighbor = _nearest_matching_cited_calculation(segments, index)
            if neighbor is None:
                continue
            shared = set(_CITATION_PATTERN.findall(segments[neighbor]))
        if shared:
            citations = " ".join(f"[{value}]" for value in sorted(shared))
            inherited[index] = f"{segment} {citations}"
    return inherited


def _nearest_cited_factual_segment(
    segments: list[str],
    index: int,
    *,
    direction: int,
) -> int | None:
    """Find an adjacent cited fact without crossing another factual claim."""
    cursor = index + direction
    structural_gap = 0
    list_gap = 0
    while 0 <= cursor < len(segments):
        candidate = segments[cursor]
        cited = bool(_CITATION_PATTERN.search(candidate))
        # Quantity-setup list items are structural, but they belong to the list
        # block budget rather than the single heading/preamble gap.
        if not cited and _is_markdown_list_item(candidate):
            list_gap += 1
            if list_gap > _MAX_CITATION_INHERITANCE_LIST_GAP:
                return None
            cursor += direction
            continue
        if _is_non_factual_segment(candidate):
            structural_gap += 1
            if structural_gap > _MAX_CITATION_INHERITANCE_STRUCTURAL_GAP:
                return None
            cursor += direction
            continue
        return cursor if cited else None
    return None


def _nearest_matching_cited_calculation(segments: list[str], index: int) -> int | None:
    """Inherit from one adjacent cited equation whose result this claim restates."""
    amounts = _amount_set(segments[index])
    if not amounts:
        return None
    for direction in (-1, 1):
        neighbor = _nearest_cited_factual_segment(segments, index, direction=direction)
        if neighbor is None:
            continue
        parsed = _parse_calculation(segments[neighbor])
        if parsed is None:
            continue
        base, rate, result = parsed
        if _restates_cited_calculation(amounts, base, rate, result):
            return neighbor
    return None


def _restates_cited_calculation(
    amounts: set[float],
    base: float,
    rate: float,
    result: float,
) -> bool:
    """Accept a wrap-up that repeats the result, not an unrelated shared base."""
    if not _arithmetic_matches(base, rate, result):
        return False
    if not _amounts_include(amounts, result):
        return False
    allowed = (base, rate, result)
    return all(
        any(abs(amount - value) <= _amount_tolerance(value) for value in allowed)
        for amount in amounts
    )


def _is_non_factual_segment(text: str) -> bool:
    return (
        _is_structural_segment(text)
        or _is_short_stance_segment(text)
        or _is_insufficiency_statement(text)
    )


def _is_short_stance_segment(text: str) -> bool:
    """Ignore a meta-level verdict; the factual correction remains verified."""
    return bool(_SHORT_STANCE_PATTERN.fullmatch(text.strip()))


def _is_structural_segment(text: str) -> bool:
    """Exclude Markdown scaffolding and list preambles that do not assert a fact."""
    stripped = text.strip()
    return bool(
        _MARKDOWN_HEADING_PATTERN.fullmatch(stripped)
        or _MARKDOWN_ORDINAL_PATTERN.fullmatch(stripped)
        or _MARKDOWN_TABLE_DIVIDER_PATTERN.fullmatch(stripped)
        or _LIST_PREAMBLE_PATTERN.fullmatch(stripped)
        or _is_quantity_setup_segment(stripped)
        or _POLARITY_PATTERN.fullmatch(stripped)
    )


def _fold_indic_digits(text: str) -> str:
    return text.translate(_BENGALI_DIGIT_FOLD)


def _plain_claim_text(text: str) -> str:
    stripped = _CITATION_PATTERN.sub("", text)
    return regex.sub(r"[*_`]+", "", stripped).strip()


def _is_markdown_list_item(text: str) -> bool:
    return bool(_MARKDOWN_LIST_ITEM_PATTERN.match(_plain_claim_text(text)))


def _is_quantity_setup_segment(text: str) -> bool:
    """Treat labeled amounts as calculation setup, not independent corpus claims."""
    folded = _fold_indic_digits(_plain_claim_text(text))
    if _CALCULATION_OPERATOR_PATTERN.search(folded):
        return False
    return bool(_QUANTITY_SETUP_PATTERN.fullmatch(folded))


def _parse_amount(value: str) -> float:
    return float(value.replace(",", ""))


def _amount_tolerance(value: float) -> float:
    return max(0.5, abs(value) * 0.005)


def _arithmetic_matches(base: float, rate: float, result: float) -> bool:
    expected = base * rate / 100.0
    return abs(expected - result) <= _amount_tolerance(expected)


def _amount_set(text: str) -> set[float]:
    folded = _fold_indic_digits(_plain_claim_text(text))
    return {_parse_amount(match) for match in _AMOUNT_PATTERN.findall(folded)}


def _amounts_include(amounts: set[float], value: float) -> bool:
    tolerance = _amount_tolerance(value)
    return any(abs(value - other) <= tolerance for other in amounts)


def _parse_calculation(text: str) -> tuple[float, float, float] | None:
    folded = _fold_indic_digits(_plain_claim_text(text))
    match = _CALCULATION_PATTERN.search(folded)
    if match is None:
        return None
    return (
        _parse_amount(match.group("base")),
        _parse_amount(match.group("rate")),
        _parse_amount(match.group("result")),
    )


def _rate_in_evidence(rate: float, evidence_texts: list[str]) -> bool:
    folded_evidence = _fold_indic_digits(" ".join(evidence_texts))
    rendered = f"{rate:g}"
    markers = (f"{rendered}%", f"{rendered} %")
    return any(marker in folded_evidence for marker in markers)


def _rates_in_evidence(evidence_texts: list[str]) -> tuple[float, ...]:
    folded_evidence = _fold_indic_digits(" ".join(evidence_texts))
    found = (_parse_amount(match) for match in _EVIDENCE_RATE_PATTERN.findall(folded_evidence))
    return tuple(dict.fromkeys(found))


def _derived_calculation_verification(
    text: str,
    evidence_texts: list[str],
    *,
    adjacent_texts: tuple[str, ...] = (),
) -> ClaimVerification | None:
    """Verify rate x amount arithmetic against cited evidence.

    User-supplied operands are not required to appear in the corpus. The cited
    rate must, and the arithmetic must evaluate. A conclusion that restates
    the result of an adjacent cited calculation is verified the same way.
    """
    parsed = _parse_calculation(text)
    if parsed is not None:
        base, rate, result = parsed
        if not _arithmetic_matches(base, rate, result):
            return ClaimVerification.UNSUPPORTED
        if not _rate_in_evidence(rate, evidence_texts):
            return ClaimVerification.UNSUPPORTED
        return ClaimVerification.SUPPORTED
    pair = _derived_amount_pair_verification(text, evidence_texts)
    if pair is not None:
        return pair
    return _derived_adjacent_result_verification(text, evidence_texts, adjacent_texts)


def _derived_adjacent_result_verification(
    text: str,
    evidence_texts: list[str],
    adjacent_texts: tuple[str, ...],
) -> ClaimVerification | None:
    amounts = _amount_set(text)
    if not amounts or not adjacent_texts:
        return None
    matched = False
    for neighbor in adjacent_texts:
        parsed = _parse_calculation(neighbor)
        if parsed is None:
            continue
        base, rate, result = parsed
        if not _restates_cited_calculation(amounts, base, rate, result):
            continue
        matched = True
        if _rate_in_evidence(rate, evidence_texts):
            return ClaimVerification.SUPPORTED
    if matched:
        return ClaimVerification.UNSUPPORTED
    return None


def _derived_amount_pair_verification(
    text: str,
    evidence_texts: list[str],
) -> ClaimVerification | None:
    amounts = sorted(_amount_set(text), reverse=True)
    if len(amounts) < 2:
        return None
    rates = _rates_in_evidence(evidence_texts)
    if not rates:
        return None
    for index, base in enumerate(amounts):
        for result in amounts[index + 1 :]:
            if any(_arithmetic_matches(base, rate, result) for rate in rates):
                return ClaimVerification.SUPPORTED
    return None


def _is_insufficiency_statement(text: str) -> bool:
    """Prompted refusals are not factual claims about the corpus."""
    folded = text.casefold()
    return _INSUFFICIENCY_MARKER in folded


def _evidence_snapshot(
    citation_index: int,
    chunk: ContextChunk,
    config: ChatConfig,
) -> ClaimEvidence:
    excerpt = (
        chunk.content[: config.citation_excerpt_max_chars]
        if config.citation_excerpt_max_chars > 0
        else None
    )
    is_web = chunk.metadata.get("source_kind") == CitationSourceKind.WEB.value
    return ClaimEvidence(
        citation_index=citation_index,
        chunk_id=None if is_web else chunk.chunk_id,
        document_id=None if is_web else chunk.document_id,
        filename=chunk.filename,
        chunk_index=None if is_web else chunk.chunk_index,
        page_number=None if is_web else chunk.page_number,
        char_start=None if is_web else chunk.char_start,
        char_end=None if is_web else chunk.char_end,
        excerpt=excerpt,
        evidence_unit_id=None if is_web else chunk.metadata.get("evidence_unit_id"),
        evidence_span_hash=None if is_web else chunk.metadata.get("evidence_span_hash"),
        source_kind=CitationSourceKind.WEB if is_web else CitationSourceKind.KNOWLEDGE,
        web_url=chunk.metadata.get("web_url") if is_web else None,
        web_title=chunk.metadata.get("web_title") if is_web else None,
        web_retrieved_at=chunk.metadata.get("web_retrieved_at") if is_web else None,
        web_provider=chunk.metadata.get("web_provider") if is_web else None,
    )


def _significant_tokens(text: str) -> set[str]:
    return {
        token
        for token in tokenize(_fold_indic_digits(text), for_query=True)
        if token not in _QUERY_SCAFFOLDING
    }


def _uses_lexical_verification(claim: str, evidence_texts: list[str]) -> bool:
    """Same-script claims keep the lexical validator; mixed scripts do not."""
    if not evidence_texts:
        return False
    return all(_same_language(claim, text) for text in evidence_texts)


def _same_language(claim: str, evidence: str) -> bool:
    claim_language = detect_language(claim)
    evidence_language = detect_language(evidence)
    if claim_language.is_mixed or evidence_language.is_mixed:
        return False
    if claim_language.primary_language is None or evidence_language.primary_language is None:
        return False
    return claim_language.primary_language == evidence_language.primary_language


def _combine_claim_verification(
    lexical: ClaimVerification | None,
    semantic: ClaimVerification | None,
) -> ClaimVerification:
    """Prefer confirmed support; do not treat missing semantic scores as a refusal."""
    if lexical is ClaimVerification.SUPPORTED or semantic is ClaimVerification.SUPPORTED:
        return ClaimVerification.SUPPORTED
    if lexical is None:
        return semantic if semantic is not None else ClaimVerification.UNVERIFIED
    if semantic is None:
        return lexical
    if lexical is ClaimVerification.UNVERIFIED:
        return semantic
    return lexical


def _usable_embedder(embedder: BaseEmbeddingProvider | None) -> bool:
    return embedder is not None and embedder.provider_name != "hash"


def _coverage(expected: set[str], actual: set[str]) -> float:
    """Raw query-token coverage. Corpus-IDF weighting was compared and not selected."""
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)


def _best_evidence(text: str, chunks: list[ContextChunk]) -> tuple[int, ContextChunk] | None:
    """Bind the strongest overlapping retrieved chunk when citations are not required."""
    ranked = [
        (_coverage(_significant_tokens(text), _significant_tokens(chunk.content)), index, chunk)
        for index, chunk in enumerate(chunks, start=1)
    ]
    if not ranked:
        return None
    score, index, chunk = max(ranked, key=lambda item: (item[0], item[2].score, -item[1]))
    return (index, chunk) if score > 0.0 else None


def _reranker_calibration_status(chunk: ContextChunk) -> str:
    provided = chunk.evidence_calibration_id
    if provided == RERANKER_RELEVANCE_CALIBRATION_ID:
        return "matched"
    if provided is None:
        return "missing_compatibility"
    return "mismatch"


def _reranker_relevance(
    chunk: ContextChunk,
    *,
    rerank_status: str | None = None,
) -> float | None:
    """Return the dedicated reranker relevance score mapped onto the chunk."""
    del rerank_status
    return chunk.rerank_relevance_score


def _rerank_applied(
    chunks: list[ContextChunk],
    *,
    rerank_status: str | None = None,
) -> bool:
    if rerank_status in {
        "skipped_same_language",
        "unavailable",
        "disabled",
        "passthrough",
        "empty",
    }:
        return False
    if rerank_status == "applied":
        return True
    return any(str(chunk.metadata.get("rerank_status")) == "applied" for chunk in chunks)
