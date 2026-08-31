"""Deterministic evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

import regex

from app.core.config import ChatConfig, EvidenceGateMode, EvidenceScoreMode
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
from app.platform.domain.language_detection import detect_language
from app.platform.domain.text_tokenization import tokenize
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingPurpose
from app.platform.providers.embedding_similarity import cosine_similarity
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
_SPAN_BOUNDARY_PATTERN = regex.compile(
    r"\n+|(?<=[.!?।॥。\uff01\uff1f…])\s+",
    regex.UNICODE,
)
_INSUFFICIENCY_MARKER = "not enough indexed evidence"
_SOURCE_NOTICE_MARKERS = (
    "this wasn\u2019t covered in the knowledge base, so i used current web sources",
    "knowledge base-এ এটি ছিল না, তাই আমি সাম্প্রতিক web সূত্র ব্যবহার করেছি",
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
    grounding_path: str = "legacy"
    shadow_candidate_wise_sufficient: bool | None = None
    shadow_candidate_wise_winning_chunk_id: uuid.UUID | None = None
    shadow_candidate_wise_admitted_count: int = 0
    legacy_sufficient: bool | None = None
    legacy_winning_chunk_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class GroundingResult:
    claims: list[dict]
    grounded: bool
    citation_coverage: float
    unverified_claim_rate: float = 0.0


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
        if self._config.candidate_wise_grounding_enabled and _rerank_applied(
            chunks, rerank_status=rerank_status
        ):
            return self.assess_candidate_wise(
                question,
                chunks,
                rerank_status=rerank_status,
            )
        return self.assess_legacy(question, chunks, rerank_status=rerank_status)

    def assess_legacy(
        self,
        question: str,
        chunks: list[ContextChunk],
        *,
        rerank_status: str | None = None,
    ) -> EvidenceDecision:
        if not chunks:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS,
            )
        if _rerank_applied(chunks, rerank_status=rerank_status):
            return self._assess_reranker_legacy(
                question,
                chunks,
                rerank_status=rerank_status,
            )
        scored = [
            item
            for chunk in chunks
            if (item := self._candidate_evidence(question, chunk)) is not None
        ]
        if not scored:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
                evidence_score_method=self._evidence_score_method,
                evidence_calibration_id=self._evidence_calibration_id,
            )
        best = max(scored, key=lambda item: (item[0], item[1], str(item[2].chunk_id)))
        direct = [
            item for item in scored if item[0] >= self._config.minimum_semantic_evidence_score
        ]
        if direct:
            winner = max(direct, key=lambda item: (item[0], item[1], str(item[2].chunk_id)))
            return self._accepted(winner)
        rescued = [
            item
            for item in scored
            if item[0] >= self._config.lexical_corroboration_floor_score
            and item[1] >= self._config.lexical_corroboration_coverage
        ]
        if rescued:
            winner = max(rescued, key=lambda item: (item[0], item[1], str(item[2].chunk_id)))
            return self._accepted(winner, lexically_corroborated=True)
        return EvidenceDecision(
            sufficient=False,
            reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
            query_token_coverage=best[1],
            best_score=best[0],
            winning_chunk_id=best[2].chunk_id,
            evidence_score_method=self._evidence_score_method,
            evidence_calibration_id=self._evidence_calibration_id,
            evidence_char_start=best[3],
            evidence_char_end=best[4],
            winning_semantic_score=best[2].semantic_score,
            winning_rank_score=best[2].rank_score,
        )

    def _assess_reranker_legacy(
        self,
        question: str,
        chunks: list[ContextChunk],
        *,
        rerank_status: str | None = None,
    ) -> EvidenceDecision:
        scored = [
            (score, chunk)
            for chunk in chunks
            if (score := _reranker_relevance(chunk, rerank_status=rerank_status)) is not None
        ]
        method = "reranker_relevance"
        calibration_id = RERANKER_RELEVANCE_CALIBRATION_ID
        if not scored:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
                evidence_score_method=method,
                evidence_calibration_id=calibration_id,
                best_score=None,
            )
        best_score = max(score for score, _ in scored)
        winner_score, winner_chunk = next(item for item in scored if item[0] == best_score)
        corroborated, coverage, lexical = self._independent_corroboration(question, winner_chunk)
        if winner_score >= self._config.minimum_reranker_evidence_score and corroborated:
            return EvidenceDecision(
                sufficient=True,
                query_token_coverage=coverage,
                best_score=winner_score,
                lexically_corroborated=lexical,
                winning_chunk_id=winner_chunk.chunk_id,
                evidence_score_method=method,
                evidence_calibration_id=calibration_id,
                evidence_char_start=winner_chunk.char_start,
                evidence_char_end=winner_chunk.char_end,
                winning_semantic_score=winner_chunk.semantic_score,
                winning_rank_score=winner_chunk.rank_score,
            )
        return EvidenceDecision(
            sufficient=False,
            reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
            query_token_coverage=coverage,
            best_score=winner_score,
            lexically_corroborated=lexical,
            winning_chunk_id=winner_chunk.chunk_id,
            evidence_score_method=method,
            evidence_calibration_id=calibration_id,
            evidence_char_start=winner_chunk.char_start,
            evidence_char_end=winner_chunk.char_end,
            winning_semantic_score=winner_chunk.semantic_score,
            winning_rank_score=winner_chunk.rank_score,
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
            return replace(
                self.assess_legacy(question, chunks, rerank_status=rerank_status),
                grounding_path="legacy_no_reranker",
            )

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

        winner = next((item for item in assessments if item.passed), assessments[0])
        winning_unit = next(
            (unit for unit in units if unit.chunk_id == winner.chunk_id),
            None,
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
            best_score=winner.reranker_score,
            lexically_corroborated=winner.corroboration_method
            in {
                "original_lexical",
                "translated_lexical",
            },
            winning_chunk_id=winner.chunk_id,
            evidence_score_method="reranker_relevance",
            evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
            evidence_char_start=(
                winning_unit.evidence_char_start if winning_unit is not None else None
            ),
            evidence_char_end=(
                winning_unit.evidence_char_end if winning_unit is not None else None
            ),
            winning_semantic_score=winner.original_semantic_score,
            winning_rank_score=(winning_unit.rank_score if winning_unit is not None else None),
            admitted_units=tuple(units),
            candidate_assessments=tuple(assessments),
            grounding_path="candidate_wise",
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
        provided_calibration = chunk.evidence_calibration_id
        calibration_status = (
            "matched"
            if provided_calibration == RERANKER_RELEVANCE_CALIBRATION_ID
            else "missing_compatibility"
            if provided_calibration is None
            else "mismatch"
        )
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
                and semantic_score >= self._config.lexical_corroboration_floor_score
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
            translated_dense_shadow_scores=translated_dense_scores,
            corroboration_method=corroboration,
            query_variant_provenance_missing=provenance_missing,
            passed=passed,
            terminal_reason=terminal_reason,
        )
        return assessment, unit

    def _independent_corroboration(
        self,
        question: str,
        chunk: ContextChunk,
    ) -> tuple[bool, float, bool]:
        """Require a non-rerank signal so forced ranking cannot admit unrelated hits.

        Query-token coverage is a valid independent signal on the reranker path,
        including when semantic cosine is missing (keyword-only / hash embeddings).
        Semantic cosine can still admit or rescue on its own calibrated bars.
        """
        item = self._candidate_evidence(question, chunk)
        evidence_text = chunk.content
        semantic: float | None = None
        if item is None:
            coverage = _coverage(
                _significant_tokens(question),
                _significant_tokens(evidence_text),
            )
        else:
            semantic, coverage, _, start, end = item
            if (
                self._config.evidence_score_mode is EvidenceScoreMode.PASSAGE_MAX
                and start is not None
                and end is not None
                and 0 <= start < end <= len(chunk.content)
            ):
                evidence_text = chunk.content[start:end]
        direct = semantic is not None and semantic >= self._config.minimum_semantic_evidence_score
        lexical = coverage >= self._config.lexical_corroboration_coverage
        cross_language = (
            not _same_language(question, evidence_text)
            and semantic is not None
            and semantic >= self._config.lexical_corroboration_floor_score
        )
        return direct or lexical or cross_language, coverage, lexical and not direct

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
        return {
            "mode": self._config.evidence_gate_mode.value,
            "sufficient": decision.sufficient,
            "reason": reason,
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
            "reranker_threshold": self._config.minimum_reranker_evidence_score,
            "winning_char_start": decision.evidence_char_start,
            "winning_char_end": decision.evidence_char_end,
            "winning_evidence_unit_id": (
                admitted_units[0].evidence_unit_id if admitted_units else None
            ),
            "winning_span_hash": (admitted_units[0].evidence_span_hash if admitted_units else None),
            "candidate_wise": {
                "enabled": self._config.candidate_wise_grounding_enabled,
                "path": decision.grounding_path,
                "shadow_sufficient": decision.shadow_candidate_wise_sufficient,
                "shadow_winning_chunk_id": (
                    str(decision.shadow_candidate_wise_winning_chunk_id)
                    if decision.shadow_candidate_wise_winning_chunk_id is not None
                    else None
                ),
                "shadow_admitted_count": decision.shadow_candidate_wise_admitted_count,
                "legacy_sufficient": decision.legacy_sufficient,
                "legacy_winning_chunk_id": (
                    str(decision.legacy_winning_chunk_id)
                    if decision.legacy_winning_chunk_id is not None
                    else None
                ),
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

    def _accepted(
        self,
        winner: tuple[float, float, ContextChunk, int | None, int | None],
        *,
        lexically_corroborated: bool = False,
    ) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=True,
            query_token_coverage=winner[1],
            best_score=winner[0],
            lexically_corroborated=lexically_corroborated,
            winning_chunk_id=winner[2].chunk_id,
            evidence_score_method=self._evidence_score_method,
            evidence_calibration_id=self._evidence_calibration_id,
            evidence_char_start=winner[3],
            evidence_char_end=winner[4],
            winning_semantic_score=winner[2].semantic_score,
            winning_rank_score=winner[2].rank_score,
        )

    @property
    def _evidence_score_method(self) -> str:
        if self._config.evidence_score_mode is EvidenceScoreMode.PASSAGE_MAX:
            return "bounded_token_max_v1"
        return "whole_chunk_cosine"

    @property
    def _evidence_calibration_id(self) -> str:
        return f"{self._evidence_score_method}:v1"

    def _candidate_evidence(
        self,
        question: str,
        chunk: ContextChunk,
    ) -> tuple[float, float, ContextChunk, int | None, int | None] | None:
        if self._config.evidence_score_mode is EvidenceScoreMode.PASSAGE_MAX:
            score = chunk.passage_semantic_score
            start = chunk.passage_char_start
            end = chunk.passage_char_end
            evidence_text = (
                chunk.content[start:end]
                if score is not None
                and start is not None
                and end is not None
                and 0 <= start < end <= len(chunk.content)
                else chunk.content
            )
        else:
            score = chunk.semantic_score
            start = chunk.char_start
            end = chunk.char_end
            evidence_text = chunk.content
        if score is None:
            return None
        coverage = _coverage(
            _significant_tokens(question),
            _significant_tokens(evidence_text),
        )
        return score, coverage, chunk, start, end

    async def map_claims(
        self,
        answer: str,
        chunks: list[ContextChunk],
        *,
        require_citations: bool = True,
    ) -> GroundingResult:
        drafts: list[_ClaimDraft] = []
        semantic_pairs: list[tuple[str, str]] = []
        for index, raw_segment in enumerate(_answer_segments(answer), start=1):
            segment = raw_segment.strip()
            if not segment:
                continue
            citation_indexes = [int(value) for value in _CITATION_PATTERN.findall(segment)]
            claim_text = _CITATION_PATTERN.sub("", segment).strip()
            if (
                not claim_text
                or _is_structural_segment(claim_text)
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
                uses_lexical = _uses_lexical_verification(draft.text, evidence_texts)
                lexical = (
                    self._lexical_verification(draft.text, evidence_texts) if uses_lexical else None
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
        "translated_dense_shadow_scores": dict(assessment.translated_dense_shadow_scores),
        "corroboration_method": assessment.corroboration_method,
        "query_variant_provenance_missing": assessment.query_variant_provenance_missing,
        "passed": assessment.passed,
        "terminal_reason": assessment.terminal_reason,
    }


def _answer_segments(answer: str) -> list[str]:
    """Keep citations written after sentence punctuation with the preceding claim."""
    segments: list[str] = []
    for raw_segment in _SEGMENT_PATTERN.split(answer):
        segment = raw_segment.strip()
        if not segment:
            continue
        leading = _LEADING_CITATIONS_PATTERN.match(segment)
        if leading is not None and segments:
            segments[-1] = f"{segments[-1]} {leading.group(1).strip()}"
            segment = leading.group(2).strip()
        if segment:
            segments.append(segment)
    return segments


def _is_structural_segment(text: str) -> bool:
    """Exclude Markdown scaffolding and list preambles that do not assert a fact."""
    stripped = text.strip()
    return bool(
        _MARKDOWN_HEADING_PATTERN.fullmatch(stripped)
        or _MARKDOWN_ORDINAL_PATTERN.fullmatch(stripped)
        or _MARKDOWN_TABLE_DIVIDER_PATTERN.fullmatch(stripped)
        or _LIST_PREAMBLE_PATTERN.fullmatch(stripped)
        or _POLARITY_PATTERN.fullmatch(stripped)
    )


def _is_insufficiency_statement(text: str) -> bool:
    """Prompted refusals are not factual claims about the corpus."""
    folded = text.casefold()
    return _INSUFFICIENCY_MARKER in folded or any(
        marker in folded for marker in _SOURCE_NOTICE_MARKERS
    )


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
    return {token for token in tokenize(text, for_query=True) if token not in _ENGLISH_STOPWORDS}


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


def _reranker_relevance(
    chunk: ContextChunk,
    *,
    rerank_status: str | None = None,
) -> float | None:
    """Return the Cohere relevance score, never a missing-value default of 0.0."""
    if chunk.rerank_relevance_score is not None:
        return chunk.rerank_relevance_score
    if (
        chunk.evidence_relevance_score is not None
        and chunk.evidence_score_method == "reranker_relevance"
    ):
        return chunk.evidence_relevance_score
    applied = rerank_status == "applied" or str(chunk.metadata.get("rerank_status")) == "applied"
    if applied and chunk.score > 0.0:
        # Successful rerank overwrites CandidateHit.score with model relevance. If the
        # dedicated field was dropped in provenance, that score is still the signal.
        return chunk.score
    return None


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
