"""Deterministic evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, cast

import regex

from app.core.config import ChatConfig, EvidenceGateMode, GroundingMode
from app.modules.conversations.citation_snapshots import proof_preview
from app.modules.conversations.ports import (
    CandidateEvidenceAssessment,
    ContextChunk,
    EvidenceUnit,
)
from app.modules.conversations.quantities import NUMBER_TOKEN, Quantity, normalize_quantities
from app.modules.conversations.schemas.message import (
    AnswerClaim,
    CitationSourceKind,
    ClaimEvidence,
    ClaimVerification,
    ClaimVerificationReason,
    InsufficientEvidenceReason,
)
from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
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
_TABLE_HEADER_SENTINEL = "\u2063table-header\u2063"
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
_COVERAGE_SCOPE_PATTERN = regex.compile(
    r"(?:available (?:materials|provisions) (?:do not|did not) establish|"
    r"the materials reviewed (?:do not|did not) establish|"
    r"(?:i|we) cannot responsibly state from (?:these|the) materials|"
    r"not enough indexed evidence|complete coverage has not been verified|"
    r"reviewed (?:evidence|materials|sources|provisions|passage(?:s)?) "
    r"(?:do(?:es)?|did) not|"
    r"this answer (?:does|did) not (?:cover|establish)|"
    r"selected (?:evidence|passages|materials) (?:do(?:es)?|did) not|"
    r"(?:supplied|provided|these) (?:source(?:s)?|evidence|passages|materials|provisions) "
    r"(?:do(?:es)?|did) not (?:establish|provide|cover)|"
    r"(?:the|this|that|cited)(?: cited)? (?:provision|source|passage|rule) "
    r"(?:do(?:es)?|did) not (?:establish|show|confirm)|"
    r"not established from the (?:available|selected|reviewed)|"
    r"outside the (?:reviewed|selected) evidence|"
    r"available (?:sources|passages|excerpts|citations) "
    r"(?:do(?:es)?|did) not (?:establish|show|confirm)|"
    r"উপলব্ধ (?:উপাদান|প্রমাণ)[^\n]{0,40}প্রতিষ্ঠিত হ[য়য়] না|"
    r"উপলভ্য (?:উদ্ধৃতি\p{Bengali}*|সূত্র\p{Bengali}*|প্রমাণ\p{Bengali}*)"
    r"[^\n]{0,100}(?:নিশ্চিতভাবে বলা যা(?:য়|য়) না|প্রতিষ্ঠিত হ(?:য়|য়) না)|"
    r"পর্যালোচিত (?:প্রমাণ|উপাদান|সূত্র)[^\n]{0,40}না|"
    r"যথেষ্ট সূচকীকৃত প্রমাণ নাই)",
    regex.IGNORECASE,
)
_COVERAGE_CONTINUATION_PATTERN = regex.compile(
    r"^(?:it|they|these|those|the same (?:sources|materials|provisions)) "
    r"(?:also )?(?:do(?:es)?|did) not (?:establish|provide|cover|show|confirm)\b",
    regex.IGNORECASE,
)
_COVERAGE_SUMMARY_PATTERN = regex.compile(
    r"^(?:therefore,?\s+)?this is (?:a )?(?:supported|evidence-based) "
    r"(?:baseline |partial )?(?:overview|answer),? not (?:a )?complete\b",
    regex.IGNORECASE,
)
_WHOLE_CORPUS_ABSENCE_PATTERN = regex.compile(
    r"(?:no provision exists(?:\s+anywhere)?|"
    r"(?:the )?(?:corpus|index) contains no|"
    r"nowhere in (?:the )?(?:corpus|index|materials)|"
    r"does not exist anywhere|"
    r"there is no (?:such )?(?:provision|rule) (?:anywhere|in (?:the )?(?:corpus|index))|"
    r"কর্পাসে কোনো|"
    r"সূচকে কোনো বিধান নাই)",
    regex.IGNORECASE,
)
# _SOURCE_NOTICE_MARKERS removed in Phase 3: web-fallback notice text is no longer
# prepended to answer content; it is a structured Notice metadata field instead.
_MAX_CITATION_INHERITANCE_STRUCTURAL_GAP = 1
_MAX_CITATION_INHERITANCE_LIST_GAP = 8
_BENGALI_DIGIT_FOLD = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_MARKDOWN_LIST_ITEM_PATTERN = regex.compile(r"^\s*(?:[-*+]|\p{Number}+[.)])\s+\S")
_QUANTITY_SETUP_PATTERN = regex.compile(
    r"^(?:[-*+]\s+)?.+[:：]\s*(?:[A-Za-z]{1,6}\s+)*[\d,.]+\s*"  # noqa: RUF001
    r"(?:[A-Za-z\p{Bengali}]{0,12})?\s*$"
)
_SCENARIO_INPUT_PATTERN = regex.compile(
    r"^(?:[-*+]\s+)?(?:eligible\s+investment|"
    r"using\s+(?:your\s+)?(?:eligible\s+)?(?:investment|amount)|"
    r"you\s+(?:provided|declared|gave|supplied)|"
    r"your\s+(?:eligible\s+)?investment|"
    r"\u09af\u09cb\u0997\u09cd\u09af\s+\u09ac\u09bf\u09a8\u09bf\u09af\u09bc?\u09cb\u0997"
    r")\b.+$",
    regex.IGNORECASE,
)
_CURRENCY_TOKEN = r"(?:[A-Za-z]{1,6}\s+|৳\s*)?"
_NUMBER = NUMBER_TOKEN
_EVIDENCE_RATE_PATTERN = regex.compile(r"(\d+(?:\.\d+)?)\s*%")
_BAND_WIDTH_PATTERN = regex.compile(
    r"(?:\bnext|পরবর্তী)\s+(?:[A-Z]{3}\s+)?(?P<width>\d[\d,]*(?:\.\d+)?)"
    r"[^|\n%]{0,40}\|\s*(?P<rate>\d+(?:\.\d+)?)\s*%",
    regex.IGNORECASE,
)
_EQUALS = r"(?:=|\uff1d|equals|is)"
_CALCULATION_PATTERNS = (
    regex.compile(
        rf"{_CURRENCY_TOKEN}(?P<base>{_NUMBER})\s*[\u00d7x*]\s*(?P<rate>{_NUMBER})\s*%\s*"
        rf"{_EQUALS}\s*{_CURRENCY_TOKEN}(?P<result>{_NUMBER})",
        regex.IGNORECASE,
    ),
    regex.compile(
        rf"(?P<rate>{_NUMBER})\s*%\s*[\u00d7x*]\s*{_CURRENCY_TOKEN}(?P<base>{_NUMBER})\s*"
        rf"{_EQUALS}\s*{_CURRENCY_TOKEN}(?P<result>{_NUMBER})",
        regex.IGNORECASE,
    ),
    regex.compile(
        rf"(?P<rate>{_NUMBER})\s*%\s+(?:of|on)\s+{_CURRENCY_TOKEN}(?P<base>{_NUMBER})\s*"
        rf"{_EQUALS}\s*{_CURRENCY_TOKEN}(?P<result>{_NUMBER})",
        regex.IGNORECASE,
    ),
    regex.compile(
        rf"{_CURRENCY_TOKEN}(?P<base>{_NUMBER})\s+at\s+(?P<rate>{_NUMBER})\s*%\s*"
        rf"{_EQUALS}\s*{_CURRENCY_TOKEN}(?P<result>{_NUMBER})",
        regex.IGNORECASE,
    ),
)
_CALCULATION_OPERATOR_PATTERN = regex.compile(
    r"[%＝=×]|[x*]\s*\d|\d\s*%",  # noqa: RUF001
    regex.IGNORECASE,
)
_MIXED_LIMITATION_CONNECTOR = regex.compile(
    r"(?:,|;)\s*(?=(?:so|therefore|thus|consequently|but|however|yet|and)\b)|"
    r"\s+(?=(?:but|however|yet)\b)|"
    r"\s+(?=তাই|অতএব|সুতরাং|কিন্তু|তবে)",
    regex.IGNORECASE,
)
_EVIDENCE_FOLLOWUP_PATTERN = regex.compile(
    r"^(?:(?:those|these|the) (?:points?|matters?|requirements?) )?"
    r"(?:should|must) be verified with\b.+$|"
    r"^(?:verify|confirm|check) (?:this|these|those) "
    r"(?:points?|matters?|requirements?) with\b.+$|"
    r"^(?:those|these) (?:matters?|points?|requirements?) need to be checked "
    r"against (?:the )?(?:applicable )?(?:provisions?|official guidance)\b.*$|"
    r"^(?:(?:এই|এসব|ঐ|উক্ত)\s+)?(?:বিষয়|বিষয়|দিক|তথ্য|প্রয়োজনীয়তা|প্রয়োজনীয়তা)"
    r"\p{Bengali}*[^,;।]{0,120}(?:যাচাই|নিশ্চিত) কর(?:া|তে) (?:উচিত|হবে)[।.!]?$",
    regex.IGNORECASE,
)
_FOLLOWUP_CONCLUSION_CONNECTOR = regex.compile(
    r"(?:,|;|:)\s*(?=(?:therefore|so|thus|consequently|and|since|because|the|a|an|companies?)\b)|"
    r"\s+(?=(?:because|since|therefore|thus|consequently|and)\b)",
    regex.IGNORECASE,
)
_LEGAL_CONCLUSION_PATTERN = regex.compile(
    r"\b(?:exempt|exemption|unnecessary|not required|need not|does not need to|"
    r"must|shall|required to|liable|not liable)\b|"
    r"\b(?:file|filing|register|registration|pay|taxable|deadline|due|date)\b",
    regex.IGNORECASE,
)
_INDEPENDENT_FOLLOWUP_ASSERTION_PATTERN = regex.compile(
    r"\b(?:is|are|was|were|must|shall|may|can|does|do|will|applies?|"
    r"requires?|exempts?)\b",
    regex.IGNORECASE,
)
_DUTY_MARKER_PATTERN = regex.compile(
    r"\b(?:must|shall|required to|does not|cannot|is not|are not)\b|করিতে হইবে",
    regex.IGNORECASE,
)
_NECESSARY_CONDITION_PATTERN = regex.compile(
    r"\bnot\b.{0,120}\bunless\b|"
    r"\bonly if\b|"
    r"\bonly when\b|"
    r"\bis required (?:in order )?to\b|"
    r"\brequired for\b.{0,80}\bto be valid\b",
    regex.IGNORECASE,
)
_REQUIRED_FOR_VALIDITY_PATTERN = regex.compile(
    r"\brequired\b.{0,80}\bvalid\b|"
    r"\bmust\b.{0,80}\bto be valid\b",
    regex.IGNORECASE,
)
_BARE_SUFFICIENT_CONDITION_PATTERN = regex.compile(
    r"\b(?:is|are)\s+valid\s+if\b|"
    r"\bguarantees?\b|"
    r"\bsuffices?\b|"
    r"\bis enough to\b",
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
    assertion: str
    evidence_chunks: list[tuple[int, ContextChunk]]
    has_valid_citation: bool
    kind_hint: str | None = None
    assertion_id: str | None = None


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
        entailment: ClaimEntailmentService | None = None,
    ) -> None:
        self._entailment = entailment
        self._config = config
        self._embedder = embedder
        self.publication_completeness: dict[str, bool | None] = {}

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
        if decision.reason in {
            InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS,
            InsufficientEvidenceReason.UNRESOLVED_AUTHORITY,
        }:
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
        user_input: str = "",
        coverage: dict[str, Any] | None = None,
        draft_segments: list[dict[str, Any]] | None = None,
    ) -> GroundingResult:
        drafts: list[_ClaimDraft] = []
        semantic_pairs: list[tuple[str, str]] = []
        spans_by_chunk: dict[uuid.UUID, list[_SelectedSpan]] = {}
        segments = (
            [str(item["text"]) for item in draft_segments]
            if draft_segments
            else _answer_segments(_normalize_page_citations(answer, chunks))
        )
        for index, raw_segment in enumerate(segments, start=1):
            segment = raw_segment.strip()
            if not segment:
                continue
            citation_indexes = [int(value) for value in _CITATION_PATTERN.findall(segment)]
            claim_text = _CITATION_PATTERN.sub("", segment).strip()
            if not draft_segments and (
                not claim_text
                or not regex.search(r"[\p{L}\p{N}]", claim_text)
                or _is_leading_table_header(segments, index - 1)
                or _is_structural_segment(claim_text)
                or _is_quantity_setup_segment(segment)
                or _is_short_stance_segment(claim_text)
                or _is_evidence_followup_statement(claim_text)
                or (_is_insufficiency_statement(claim_text) and not coverage)
            ):
                continue
            context = _verification_context(segments, index - 1)
            assertion = _contextualized_assertion(claim_text, context)
            continuation = _preceding_continuation_context(segments, index - 1)
            if continuation:
                assertion = f"{continuation} {assertion}"
            kind_hint = _coverage_kind_hint(assertion, display=claim_text)
            if kind_hint is None and _is_bounded_coverage_continuation(
                segments, index - 1, claim_text
            ):
                kind_hint = "coverage_scope"
            if draft_segments:
                # Typed draft rows are assertions. Operational scope belongs to
                # DraftNotice and cannot acquire a coverage-verdict bypass here.
                kind_hint = None
                assertion = claim_text
            evidence_chunks = [
                (citation_index, chunks[citation_index - 1])
                for citation_index in dict.fromkeys(citation_indexes)
                if 1 <= citation_index <= len(chunks)
            ]
            if draft_segments:
                bound = draft_segments[index - 1]
                allowed = set(bound.get("requirement_ids", [])) if bound else set()
                proof_ids = set(bound.get("proof_ids", []))
                evidence_chunks = [
                    (i, chunk)
                    for i, chunk in enumerate(chunks, 1)
                    if str(chunk.chunk_id) in proof_ids
                ]
                evidence_chunks = [
                    (
                        citation_index,
                        replace(
                            chunk,
                            metadata={
                                **chunk.metadata,
                                **(
                                    {
                                        "reviewed_proof": [
                                            item
                                            for item in chunk.metadata.get("reviewed_proof", [])
                                            if item.get("requirement_id") in allowed
                                        ]
                                    }
                                    if "reviewed_proof" in chunk.metadata
                                    else {}
                                ),
                            },
                        ),
                    )
                    for citation_index, chunk in evidence_chunks
                ]
            has_valid_citation = bool(evidence_chunks)
            if kind_hint != "coverage_scope" and not evidence_chunks and not require_citations:
                best = _best_evidence(assertion, chunks)
                if best is not None:
                    evidence_chunks = [best]
            drafts.append(
                _ClaimDraft(
                    index=index,
                    text=claim_text,
                    assertion=assertion,
                    evidence_chunks=evidence_chunks,
                    has_valid_citation=has_valid_citation,
                    kind_hint=kind_hint,
                    assertion_id=str(draft_segments[index - 1]["assertion_id"])
                    if draft_segments
                    else f"A{index}",
                )
            )
            if kind_hint != "coverage_scope":
                keys = {claim_text, assertion} - {""}
                for _, chunk in evidence_chunks:
                    spans_by_chunk.setdefault(chunk.chunk_id, _claim_candidate_spans(chunk))
                    for span in _spans_for_embedding(assertion, claim_text, chunk, spans_by_chunk):
                        for key in keys:
                            semantic_pairs.append((key, span.text))
        similarities = await self._claim_similarities(semantic_pairs)
        entailment_inputs: list[dict[str, object]] = []
        for draft in drafts:
            proof: list[dict[str, object]] = []
            for _, chunk in draft.evidence_chunks:
                reviewed = [
                    item
                    for item in chunk.metadata.get("reviewed_proof", [])
                    if item.get("quote")
                    and str(item["quote"]) in chunk.content
                    and (
                        not draft_segments
                        or item.get("requirement_id")
                        in draft_segments[draft.index - 1].get("requirement_ids", [])
                    )
                ]
                proof.extend(
                    [
                        {
                            "quote": item["quote"],
                            "chunk_id": str(chunk.chunk_id),
                            "requirement_id": item.get("requirement_id"),
                            "supported_scope": item.get("supported_scope", ""),
                        }
                        for item in reviewed
                    ]
                    if "reviewed_proof" in chunk.metadata
                    else [{"quote": chunk.content, "chunk_id": str(chunk.chunk_id)}]
                )
            entailment_inputs.append(
                {
                    "assertion_id": draft.assertion_id or f"claim-{draft.index}",
                    "assertion": draft.assertion,
                    "proof": proof,
                    "scenario_input": user_input,
                }
            )
        entailments = ["unverified"] * len(drafts)
        pending: list[int] = []
        # Every non-identical assertion takes source entailment, including
        # ordinary passages. Similarity only locates candidate source spans.
        raw_positions: set[int] = set()
        exact_positions: set[int] = set()
        for position, draft in enumerate(drafts):
            proof_items = cast(list[dict[str, object]], entailment_inputs[position]["proof"])
            assertion = " ".join(_plain_claim_text(draft.assertion).split()).strip()
            exact = any(
                assertion
                == " ".join(_plain_claim_text(str(proof.get("quote", ""))).split()).strip()
                for proof in proof_items
            )
            joined_proof = " ".join(
                _plain_claim_text(str(item.get("quote", ""))) for item in proof_items
            )
            exact = exact or assertion == " ".join(joined_proof.split()).strip()
            if draft.kind_hint == "coverage_scope":
                continue
            if exact and assertion:
                # Whole-assertion identity is reusable proof; substring/embedding
                # similarity, a category change or any additional clause is not.
                entailments[position] = "supported"
                exact_positions.add(position)
            elif proof_items:
                pending.append(position)
            if self._entailment is None:
                raw_positions.add(position)
        if self._entailment and pending:
            verdicts = await self._entailment.verify([entailment_inputs[i] for i in pending])
            for position, verdict in zip(pending, verdicts, strict=True):
                entailments[position] = verdict

        verifier_failure = getattr(self._entailment, "last_failure", None)
        structured_verdicts = getattr(self._entailment, "last_verdicts", [])
        verdict_by_position = dict(zip(pending, structured_verdicts, strict=False))
        self.publication_completeness = {
            str(draft.assertion_id or f"claim-{draft.index}"): verdict_by_position.get(
                position, {}
            ).get("publication_complete")
            for position, draft in enumerate(drafts)
        }
        claims: list[AnswerClaim] = []
        for draft_position, draft in enumerate(drafts):
            kind = draft.kind_hint or _claim_kind(draft.assertion, user_input, display=draft.text)
            supporting_spans: dict[uuid.UUID, _SelectedSpan] = {}
            verification_method: str | None = None
            verification_reason: str | None = None
            if kind == "coverage_scope":
                verification, verification_reason = _coverage_scope_verification(
                    draft.assertion, coverage, display=draft.text
                )
                verification_method = "coverage_verdict"
                evidence_texts: list[str] = []
            elif not draft.evidence_chunks:
                verification = ClaimVerification.UNSUPPORTED
                verification_reason = ClaimVerificationReason.MISSING_CITATION
                evidence_texts = []
            else:
                selected_spans = _best_evidence_spans(
                    draft.assertion,
                    draft.evidence_chunks,
                    similarities,
                    {
                        chunk.chunk_id: _claim_candidate_spans(chunk)
                        for _, chunk in draft.evidence_chunks
                    },
                    display=draft.text,
                )
                supporting_spans = selected_spans
                span_texts = [span.text for span in selected_spans.values()]
                evidence_texts = [chunk.content for _, chunk in draft.evidence_chunks]
                full_evidence = " ".join(evidence_texts)
                model_entailment = (
                    self._entailment is not None and draft_position not in raw_positions
                )
                # Quantity binding needs all cited clauses.  The single semantic
                # locator span may omit a neighbouring deadline, exception, or
                # sanction clause from the same bounded source passage.
                indivisible_proof = any(
                    chunk.metadata.get("proof_unit_chunk_ids") for _, chunk in draft.evidence_chunks
                )
                quantity_evidence = (
                    full_evidence
                    if indivisible_proof
                    else " ".join(_quantity_aligned_evidence(draft.assertion, evidence_texts))
                )
                duration_evidence = quantity_evidence
                neighbor = _nearest_matching_cited_calculation(segments, draft.index - 1)
                adjacent_texts = (segments[neighbor],) if neighbor is not None else ()
                derived = (
                    None
                    if draft_segments
                    else _derived_calculation_verification(
                        draft.assertion,
                        evidence_texts,
                        adjacent_texts=adjacent_texts,
                        extra_bases=tuple(_currency_amounts(user_input)),
                        authorized_operands=_amount_set(user_input + " " + full_evidence),
                    )
                )
                graph_verified = bool(
                    draft_segments and draft_segments[draft.index - 1].get("calculation_verified")
                )
                unsupported_composite = (
                    not graph_verified
                    and "=" in draft.assertion
                    and regex.search(
                        r"\d\s*[+\u2212-]\s*\d|\b(?:min|max|sum)\s*\(",
                        draft.assertion,
                        regex.IGNORECASE,
                    )
                )
                contested_generalization = regex.search(
                    r"\b(?:consensus|majority|unanimous)\b|"
                    r"\b(?:all|most)\s+(?:of\s+the\s+)?(?:reviewed\s+)?"
                    r"(?:works|accounts|sources|authors|historians)\b|"
                    r"সংখ্যাগরিষ্ঠ|সর্বসম্মত",
                    draft.assertion,
                    regex.IGNORECASE,
                )
                if draft_position in exact_positions:
                    verification = ClaimVerification.SUPPORTED
                    verification_method = "literal_source_identity"
                elif _missing_duration(draft.assertion, duration_evidence) and (
                    _duration_context_related(draft.assertion, duration_evidence)
                    or _quantity_scope_conflict(draft.assertion, full_evidence)
                ):
                    verification = ClaimVerification.UNSUPPORTED
                    verification_method = "duration"
                    verification_reason = ClaimVerificationReason.DURATION_MISMATCH
                elif graph_verified:
                    verification = ClaimVerification(entailments[draft_position])
                    verification_method = "calculation_graph_and_entailment"
                elif unsupported_composite or contested_generalization:
                    # Similarity does not prove a consensus or a count across works.
                    verification = ClaimVerification.UNVERIFIED
                    verification_reason = ClaimVerificationReason.CONTESTED_GENERALIZATION
                elif derived is not None and draft_segments and draft_position in pending:
                    # New drafts must bind their calculation to the audited graph.
                    # Legacy standalone claim mapping remains readable.
                    verification = ClaimVerification.UNVERIFIED
                    verification_method = "calculation_graph_required"
                    verification_reason = ClaimVerificationReason.DERIVED_QUANTITY
                elif derived is not None:
                    # Decimal equality proves only arithmetic. Every legal scope
                    # and additional assertion must also pass source entailment.
                    semantic_verdict = ClaimVerification(entailments[draft_position])
                    verification = (
                        derived
                        if derived is not ClaimVerification.SUPPORTED or not model_entailment
                        else semantic_verdict
                    )
                    verification_method = "arithmetic_and_entailment"
                elif regex.search(
                    r"(?:\b(?:BDT|Tk|fee|fine|amount|payable)\b|৳)",
                    draft.assertion,
                    regex.IGNORECASE,
                ) and any(
                    not _amounts_include(
                        _evidence_money_amounts(quantity_evidence, claim=draft.assertion)
                        | (
                            _currency_amounts(user_input)
                            if kind in {"scenario_input", "arithmetic"}
                            else set()
                        ),
                        amount,
                    )
                    for amount in _currency_amounts(draft.assertion)
                ):
                    # Topic similarity cannot authenticate an invented monetary
                    # amount. User-supplied operands remain valid calculation input.
                    verification = ClaimVerification.UNSUPPORTED
                    verification_reason = ClaimVerificationReason.UNVERIFIED_AMOUNT
                elif regex.search(r"\d[^\n]*(?:[\u00d7\u00f7=]|\s[x*]\s)[^\n]*\d", draft.assertion):
                    # Similarity cannot certify calculation syntax the arithmetic verifier
                    # does not understand (e.g. a sum, nested formula or a contested total).
                    verification = ClaimVerification.UNVERIFIED
                    verification_reason = ClaimVerificationReason.UNPARSED_CALCULATION
                elif regex.search(
                    r"\b(longer|shorter|difference|increase|decrease|more|less)\b|"
                    r"(?<![\p{L}\p{M}])(?:পার্থক্য|বেশি|কম)(?![\p{L}\p{M}])",
                    draft.assertion,
                    regex.IGNORECASE,
                ) and (
                    _amount_set(draft.assertion)
                    - _explained_quantity_values(draft.assertion, full_evidence)
                ):
                    # A newly calculated quantity is not proven by topic similarity.
                    verification = ClaimVerification.UNVERIFIED
                    verification_reason = ClaimVerificationReason.DERIVED_QUANTITY
                elif regex.search(
                    (
                        "\\b(total|payable|liability|net|remaining|after|calculat"
                        "ed|result)\\b|মোট|প্রদেয়"
                    ),
                    draft.assertion,
                    regex.IGNORECASE,
                ) and any(
                    not _amounts_include(
                        _evidence_money_amounts(quantity_evidence, claim=draft.assertion),
                        amount,
                    )
                    for amount in _money_amounts(draft.assertion)
                ):
                    verification = ClaimVerification.UNVERIFIED
                    verification_reason = ClaimVerificationReason.UNVERIFIED_AMOUNT
                else:
                    scored_texts = span_texts or evidence_texts
                    uses_lexical = _uses_lexical_verification(draft.assertion, scored_texts)
                    lexical = (
                        self._lexical_verification(draft.assertion, scored_texts)
                        if uses_lexical
                        else None
                    )
                    scores = [
                        _best_pair_score(similarities, (draft.assertion, draft.text), text)
                        for text in scored_texts
                    ]
                    numeric = [value for value in scores if value is not None]
                    score = max(numeric) if numeric else None
                    embedder_usable = _usable_embedder(self._embedder)
                    semantic = (
                        self._cross_language_verification(score)
                        if not uses_lexical or embedder_usable
                        else None
                    )
                    # Embeddings only align spans. Reviewed proof takes a fresh
                    # batch entailment on ordinary and reviewed passages alike.
                    if model_entailment:
                        semantic = ClaimVerification(entailments[draft_position])
                        verification = semantic
                    else:
                        if score is not None and score < self._config.claim_semantic_reject_floor:
                            semantic = ClaimVerification.UNSUPPORTED
                        verification = _combine_claim_verification(lexical, semantic)
                    verification_method = (
                        "source_entailment"
                        if model_entailment
                        else "lexical"
                        if uses_lexical
                        else "semantic"
                    )
                    if (
                        not model_entailment
                        and uses_lexical
                        and lexical is ClaimVerification.UNSUPPORTED
                        and semantic is ClaimVerification.SUPPORTED
                    ):
                        # Same-language similarity selects the best span but cannot
                        # turn a low-overlap passage into factual entailment.
                        verification = ClaimVerification.UNVERIFIED
                        verification_method = "semantic_locator"
                        verification_reason = (
                            ClaimVerificationReason.UNRELATED_OR_INSUFFICIENT_EVIDENCE
                        )
                    entailment_evidence = (
                        full_evidence
                        if regex.search(
                            r"\bfirst\s+(?:annual\s+general\s+meeting|agm)\b",
                            draft.assertion,
                            regex.IGNORECASE,
                        )
                        and regex.search(r"\b(?:extend|extension)\b", draft.assertion)
                        else " ".join(scored_texts)
                    )
                    entailment_guard = _bounded_entailment_guard(
                        draft.assertion, entailment_evidence
                    )
                    if entailment_guard is not None:
                        verification = entailment_guard
                        verification_method = "bounded_entailment"
                        verification_reason = (
                            ClaimVerificationReason.UNRELATED_OR_INSUFFICIENT_EVIDENCE
                        )
                    if (
                        semantic is ClaimVerification.UNVERIFIED
                        and score is None
                        and verification_reason is None
                    ):
                        verification_reason = ClaimVerificationReason.EMBEDDING_UNAVAILABLE
                    elif verification is ClaimVerification.UNSUPPORTED:
                        verification_reason = (
                            ClaimVerificationReason.UNRELATED_OR_INSUFFICIENT_EVIDENCE
                        )
                    duration_delta = _missing_duration(draft.assertion, duration_evidence)
                    if duration_delta and verification is ClaimVerification.SUPPORTED:
                        # Related evidence with a different duration is a contradiction;
                        # related evidence with no duration is merely incomplete.
                        verification = (
                            ClaimVerification.UNSUPPORTED
                            if _duration_quantities(duration_evidence)
                            else ClaimVerification.UNVERIFIED
                        )
                        verification_method = "duration"
                        verification_reason = (
                            ClaimVerificationReason.DURATION_MISMATCH
                            if verification is ClaimVerification.UNSUPPORTED
                            else ClaimVerificationReason.DURATION_NOT_IN_EVIDENCE
                        )
                    elif (
                        duration_delta
                        and verification is ClaimVerification.UNVERIFIED
                        and _duration_quantities(duration_evidence)
                    ):
                        # When semantic verification is unavailable, an explicit changed
                        # duration still must not become an unscored maybe.
                        verification = ClaimVerification.UNSUPPORTED
                        verification_method = "duration"
                        verification_reason = ClaimVerificationReason.DURATION_MISMATCH
                    elif duration_delta and verification is ClaimVerification.UNVERIFIED:
                        verification_reason = verification_reason or (
                            ClaimVerificationReason.DURATION_NOT_IN_EVIDENCE
                        )
            if (
                verifier_failure
                and verification is not ClaimVerification.SUPPORTED
                and draft_position in pending
            ):
                verification_reason = verifier_failure["reason"]
                verification_method = "source_entailment"
            if draft_position not in exact_positions and _bound_period_amount_conflict(
                draft.assertion, evidence_texts
            ):
                verification = ClaimVerification.UNSUPPORTED
                verification_reason = ClaimVerificationReason.UNVERIFIED_AMOUNT
                verification_method = "period_amount_binding"
            proposal = any(
                chunk.metadata.get("source_type") == "budget_speech"
                or "proposal" in str(chunk.metadata.get("source_title", "")).casefold()
                for _, chunk in draft.evidence_chunks
            )
            if proposal and _proposal_quantity_role_conflict(draft.assertion, evidence_texts):
                verification = ClaimVerification.UNSUPPORTED
                verification_reason = ClaimVerificationReason.UNRELATED_OR_INSUFFICIENT_EVIDENCE
                verification_method = "proposal_quantity_role"
            if proposal and regex.search(
                r"\b(?:enacted|current law|operative law|never enacted|not enacted)\b",
                draft.assertion,
                regex.IGNORECASE,
            ):
                verification = ClaimVerification.UNSUPPORTED
                verification_reason = ClaimVerificationReason.UNRELATED_OR_INSUFFICIENT_EVIDENCE
                verification_method = "proposal_legal_effect"
            # Lexical/semantic similarity and correct arithmetic do not resolve
            # amendment scope. Do not give these claims a false green status.
            evidence_support = verification
            arithmetic = (
                ClaimVerification.SUPPORTED
                if draft_segments and draft_segments[draft.index - 1].get("calculation_verified")
                else _arithmetic_consistency(draft.assertion)
            )
            if arithmetic is not None and arithmetic is not ClaimVerification.SUPPORTED:
                verification = ClaimVerification.UNSUPPORTED
                verification_reason = ClaimVerificationReason.ARITHMETIC_MISMATCH
            authority_unresolved = any(
                chunk.metadata.get("authority_status") == "unresolved"
                for _, chunk in draft.evidence_chunks
            )
            if verification is ClaimVerification.SUPPORTED and authority_unresolved:
                verification = ClaimVerification.UNVERIFIED
                verification_reason = ClaimVerificationReason.UNRESOLVED_AUTHORITY
            claims.append(
                AnswerClaim(
                    evidence_support=evidence_support,
                    authority_status="unresolved" if authority_unresolved else "not_assessed",
                    claim_kind=kind,
                    arithmetic_verification=arithmetic,
                    verification_method=verification_method,
                    verification_reason=(
                        None if verification_reason is None else str(verification_reason)
                    ),
                    assertion_text=draft.assertion if draft.assertion != draft.text else None,
                    assertion_id=draft.assertion_id,
                    verifier_failures=[
                        {
                            "assertion_id": draft.assertion_id or f"claim-{draft.index}",
                            "dimension": dimension,
                            "evidence_binding": [
                                str(chunk.chunk_id) for _, chunk in draft.evidence_chunks
                            ],
                        }
                        for dimension in (
                            verdict_by_position.get(draft_position, {}).get("failed_dimensions")
                            or [
                                "quantity_role"
                                if verification_method in {"numeric", "duration", "arithmetic"}
                                else "scope"
                            ]
                        )
                    ]
                    if verification is not ClaimVerification.SUPPORTED
                    else [],
                    calculation_references=list(
                        draft_segments[draft.index - 1].get("calculation_references", [])
                    )
                    if draft_segments
                    else [],
                    requirement_ids=list(
                        dict.fromkeys(
                            draft_segments[draft.index - 1].get("requirement_ids", [])
                            if draft_segments
                            else (
                                str(item["requirement_id"])
                                for _, chunk in draft.evidence_chunks
                                for item in chunk.metadata.get("reviewed_proof", [])
                                if item.get("requirement_id")
                            )
                        )
                    ),
                    claim_id=f"claim-{draft.index}",
                    text=draft.text,
                    grounded=verification is ClaimVerification.SUPPORTED,
                    verification=verification,
                    evidence=[
                        _evidence_snapshot(
                            citation_index,
                            chunk,
                            self._config,
                            span=proof_span,
                        )
                        for citation_index, chunk in draft.evidence_chunks
                        for proof_span in _reviewed_claim_spans(
                            chunk,
                            supporting_spans.get(chunk.chunk_id),
                            requirement_ids=set(
                                draft_segments[draft.index - 1].get("requirement_ids", [])
                            )
                            if draft_segments
                            else None,
                        )
                    ],
                )
            )
        return _grounding_result_from_claims(
            claims,
            cited_factual=sum(
                draft.has_valid_citation
                for draft, claim in zip(drafts, claims, strict=True)
                if claim.claim_kind != "coverage_scope"
            ),
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
    if chunk.metadata.get("element_type") == "table":
        # A numeric passage can score highly while excluding the table's scope.
        # Admit the whole table unit or omit it; do not transfer a passage score
        # to a larger span that was never scored.
        if len(chunk.content) > max_chars:
            return None
        return _SelectedSpan(
            text=chunk.content,
            char_start=0,
            char_end=len(chunk.content),
            derivation="complete_chunk",
            semantic_score=chunk.semantic_score,
            semantic_span_aligned=True,
        )
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
    if (
        chunk.metadata.get("table_id")
        or chunk.metadata.get("table_context")
        or chunk.metadata.get("table_row_group")
        or chunk.metadata.get("element_type") == "table"
    ):
        # Headers, category/period cells, continuation and footnotes in the
        # hydrated bounded chunk travel together; ranking cannot detach a row.
        span = replace(
            span,
            text=chunk.content,
            char_start=0,
            char_end=len(chunk.content),
            derivation="structured_table_proof",
        )
    span_hash = content_hash(span.text)
    unit_id = content_hash(
        f"evidence-unit:v1:{chunk.chunk_id}:{span.char_start}:{span.char_end}:{span_hash}"
    )
    document_start: int | None = (
        chunk.char_start + span.char_start if chunk.char_start is not None else span.char_start
    )
    document_end: int | None = (
        chunk.char_start + span.char_end if chunk.char_start is not None else span.char_end
    )
    if (
        chunk.metadata.get("table_context")
        or chunk.metadata.get("table_row_group")
        or chunk.metadata.get("heading_context_status") == "preserved"
    ):
        # Repeated headings/row prefixes are not a contiguous slice of the
        # parsed document. Report its source envelope; local evidence offsets and
        # hashes still identify exactly the text shown to generation.
        document_start = chunk.char_start
        document_end = chunk.char_end
    metadata = {
        **chunk.metadata,
        "evidence_unit_id": unit_id,
        "evidence_span_hash": span_hash,
        "evidence_source_chunk_hash": chunk.chunk_hash,
        "source_chunk_char_end": chunk.char_end,
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


def _quantity_number_words() -> dict[str, int]:
    words = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
        "এক": 1,
        "শূন্য": 0,
        "দুই": 2,
        "তিন": 3,
        "চার": 4,
        "পাঁচ": 5,
        "ছয়": 6,
        "ছয়": 6,
        "সাত": 7,
        "আট": 8,
        "নয়": 9,
        "নয়": 9,
        "দশ": 10,
        "এগারো": 11,
        "বারো": 12,
        "বার": 12,
        "তেরো": 13,
        "চৌদ্দ": 14,
        "পনের": 15,
        "পনেরো": 15,
        "ষোল": 16,
        "ষোলো": 16,
        "সতেরো": 17,
        "আঠারো": 18,
        "আঠার": 18,
        "উনিশ": 19,
        "বিশ": 20,
        "একুশ": 21,
        "আটাশ": 28,
        "আঠাশ": 28,
        "ত্রিশ": 30,
        "চল্লিশ": 40,
        "পঁয়তাল্লিশ": 45,
        "পঞ্চাশ": 50,
        "ষাট": 60,
        "সত্তর": 70,
        "আশি": 80,
        "নব্বই": 90,
    }
    for tens in ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"):
        for ones in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine"):
            for separator in (" ", "-"):
                words[tens + separator + ones] = words[tens] + words[ones]
    return words


def _parenthetical_number_value(spelling: str, words: dict[str, int]) -> Decimal | None:
    """Parse a complete integer or digit-spelled decimal inside source parentheses."""
    normalized = spelling.strip().casefold()
    if normalized in words:
        return Decimal(words[normalized])
    parts = regex.split(r"\s+(?:decimal|point|দশমিক)\s+", normalized)
    if len(parts) != 2 or parts[0] not in words:
        return None
    fraction = parts[1].split()
    if not fraction or any(word not in words or not 0 <= words[word] <= 9 for word in fraction):
        return None
    return Decimal(f"{words[parts[0]]}.{''.join(str(words[word]) for word in fraction)}")


def _spelled_number_values(text: str) -> set[int]:
    """Recognize isolated integer spellings without truncating larger numbers.

    This only removes a numeric-format mismatch; lexical/semantic verification
    must still establish the statement. Unsupported compound scales stay closed.
    """
    words = _quantity_number_words()
    # Archaic "বার" means twelve only with a duration unit; alone it also
    # means an occurrence and must not establish a count of twelve.
    words.pop("বার", None)
    alternatives = "|".join(regex.escape(word) for word in sorted(words, key=len, reverse=True))
    scales = (
        "(?:hundred|thousand|million|billion|lakh|crore|[\\p{L}\\p{M}]*(?:শত|শো)|হাজার|লক্ষ|লাখ|কোটি)"
    )
    values = set()
    for match in regex.finditer(
        rf"(?<![\p{{L}}\p{{M}}\d])({alternatives})(?:ের|এর)?(?![\p{{L}}\p{{M}}\d])",
        text,
        regex.IGNORECASE,
    ):
        before, after = text[: match.start()].rstrip(), text[match.end() :].lstrip()
        if regex.search(rf"{scales}(?:\s+and)?$", before, regex.IGNORECASE) or regex.match(
            rf"{scales}\b", after, regex.IGNORECASE
        ):
            continue
        values.add(words[match.group(1).lower()])
    return values


def _duration_quantities(text: str) -> set[tuple[int, str]]:
    # Legal prose commonly spells out durations while answers use digits.
    words = _quantity_number_words()
    alternatives = "|".join(regex.escape(word) for word in sorted(words, key=len, reverse=True))
    unit_tail = (
        r"(?:(?:business|working|calendar)\s+days?\b|days?\b|weeks?\b|months?\b|years?\b|"
        r"কর্মদিবস|দিন|সপ্তাহ|মাস|বছর|বৎসর|বত্সর)"
    )
    text = regex.sub(
        rf"\b({alternatives})(?=(?:\s+{unit_tail}|\s*(?:কর্মদিবস|দিন|সপ্তাহ|মাস|বছর|বৎসর|বত্সর)))",
        lambda match: str(words[match.group().lower()]),
        text,
        flags=regex.IGNORECASE,
    )
    units = {
        "business day": "business_day",
        "business days": "business_day",
        "working day": "business_day",
        "working days": "business_day",
        "calendar day": "day",
        "calendar days": "day",
        "day": "day",
        "days": "day",
        "দিন": "day",
        "কর্মদিবস": "business_day",
        "week": "week",
        "weeks": "week",
        "সপ্তাহ": "week",
        "month": "month",
        "months": "month",
        "মাস": "month",
        "year": "year",
        "years": "year",
        "বছর": "year",
        "বৎসর": "year",
        "বত্সর": "year",
    }
    return {
        (int(number), units[unit.lower()])
        for number, unit in regex.findall(
            rf"(?<![\d.,])\b(\d+)\s*({unit_tail})",
            text,
            regex.IGNORECASE,
        )
    }


def _normalize_page_citations(answer: str, chunks: list[ContextChunk]) -> str:
    """Accept page-qualified markers only when every page matches its evidence.

    Page labels in document text may differ from the indexed PDF page. Do not
    silently discard such discrepancies or interpret a page as a source index.
    Claim verification still runs after this syntax normalization.
    """
    item_pattern = regex.compile(r"\s*(\d+)\s*,\s*(?:page|p\.|পৃষ্ঠা)\s*(\d+)\s*", regex.IGNORECASE)

    def normalize(match: regex.Match) -> str:
        items = [item_pattern.fullmatch(item) for item in match.group(1).split(";")]
        if not items or any(item is None for item in items):
            return match.group(0)
        indexes: list[int] = []
        for item in items:
            assert item is not None
            index, page = int(item.group(1)), int(item.group(2))
            if not 1 <= index <= len(chunks) or chunks[index - 1].page_number != page:
                return match.group(0)
            indexes.append(index)
        return " ".join(f"[{index}]" for index in dict.fromkeys(indexes))

    return regex.sub(r"\[([^\[\]\n]+)\]", normalize, answer)


def _split_mixed_limitation_assertion(segment: str) -> list[str]:
    """Split a coverage limitation from a grammatically separate factual conclusion."""
    plain = _plain_claim_text(segment)
    if not _COVERAGE_SCOPE_PATTERN.search(plain):
        return [segment]
    match = _MIXED_LIMITATION_CONNECTOR.search(segment)
    if match is None:
        return [segment]
    left = segment[: match.start()].strip()
    right = segment[match.end() :].strip()
    if not left or not right:
        return [segment]
    citations = " ".join(
        f"[{value}]" for value in dict.fromkeys(_CITATION_PATTERN.findall(segment))
    )
    left = _CITATION_PATTERN.sub("", left).strip()
    if citations and not _CITATION_PATTERN.search(right):
        right = f"{right} {citations}".strip()
    return [left, right]


def _split_mixed_followup_assertion(segment: str) -> list[str]:
    """Keep operational advice separate from a following legal conclusion."""
    plain = _plain_claim_text(segment)
    if not _EVIDENCE_FOLLOWUP_PATTERN.match(plain):
        return [segment]
    for match in _FOLLOWUP_CONCLUSION_CONNECTOR.finditer(segment):
        left = _CITATION_PATTERN.sub("", segment[: match.start()]).strip()
        right = segment[match.end() :].strip()
        if (
            not left
            or not right
            or not _LEGAL_CONCLUSION_PATTERN.search(right)
            or not _INDEPENDENT_FOLLOWUP_ASSERTION_PATTERN.search(right)
        ):
            continue
        if not _is_evidence_followup_statement(left):
            continue
        citations = " ".join(
            f"[{value}]" for value in dict.fromkeys(_CITATION_PATTERN.findall(segment))
        )
        if citations and not _CITATION_PATTERN.search(right):
            right = f"{right} {citations}"
        return [left, right]
    return [segment]


class _AnswerSegment(str):
    """A string claim carrying private Markdown ownership metadata."""

    block_id: int
    block_kind: str

    def __new__(cls, value: str, *, block_id: int, block_kind: str) -> _AnswerSegment:
        instance = str.__new__(cls, value)
        instance.block_id = block_id
        instance.block_kind = block_kind
        return instance


def _answer_segments(answer: str) -> list[str]:
    """Split claims and safely inherit nearby citations.

    Inheritance only supplies candidate evidence.  Every sentence is still
    verified independently by ``map_claims`` before it can be grounded.
    """
    # Build real Markdown ownership blocks before sentence splitting. A wrapped
    # list item remains one block, while sibling/nested/numbered items, table
    # rows, and blank-line-separated paragraphs own their citations separately.
    lines = answer.splitlines()
    blocks: list[tuple[str, str]] = []
    pending: list[str] = []
    pending_kind = "paragraph"

    def flush() -> None:
        nonlocal pending, pending_kind
        if pending:
            blocks.append((pending_kind, " ".join(part.strip() for part in pending).strip()))
            pending = []
            pending_kind = "paragraph"

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        is_divider = "|" in line and _MARKDOWN_TABLE_DIVIDER_PATTERN.fullmatch(line.strip())
        if is_divider:
            flush()
            continue
        is_header = (
            "|" in line
            and index + 1 < len(lines)
            and "|" in lines[index + 1]
            and _MARKDOWN_TABLE_DIVIDER_PATTERN.fullmatch(lines[index + 1].strip())
        )
        if stripped.startswith("|") and stripped.endswith("|"):
            flush()
            blocks.append(("table", f"{_TABLE_HEADER_SENTINEL}{line}" if is_header else line))
        elif _starts_markdown_list_item(line):
            flush()
            pending_kind = "list"
            pending = [line]
        elif _MARKDOWN_HEADING_PATTERN.fullmatch(stripped):
            flush()
            blocks.append(("heading", stripped))
        elif pending_kind == "list":
            # CommonMark permits lazy (unindented) continuation lines. They
            # remain owned by the current item until a blank or another marker.
            pending.append(line)
        else:
            pending.append(line)
    flush()

    segments: list[str] = []
    for block_id, (block_kind, paragraph) in enumerate(blocks):
        stripped_paragraph = paragraph.strip()
        if stripped_paragraph.startswith(_TABLE_HEADER_SENTINEL) or _is_markdown_table_row(
            stripped_paragraph
        ):
            # A row is one structural assertion. Sentence punctuation inside a
            # cell must not detach an authority, condition, or citation from the
            # duty named in another cell.
            segments.append(
                _AnswerSegment(stripped_paragraph, block_id=block_id, block_kind=block_kind)
            )
            continue
        if _MARKDOWN_HEADING_PATTERN.fullmatch(paragraph.strip()):
            # Do not split a numbered heading at "1." and turn its remaining
            # title into an unsupported factual sentence.
            segments.append(
                _AnswerSegment(paragraph.strip(), block_id=block_id, block_kind=block_kind)
            )
            continue
        paragraph_segments: list[str] = []
        for raw_segment in _SEGMENT_PATTERN.split(paragraph):
            segment = raw_segment.strip()
            if not segment:
                continue
            leading = _LEADING_CITATIONS_PATTERN.match(segment)
            if leading is not None and paragraph_segments:
                paragraph_segments[-1] = f"{paragraph_segments[-1]} {leading.group(1).strip()}"
                segment = leading.group(2).strip()
            if segment and not regex.fullmatch(r"[|\s]+", segment):
                for part in _split_mixed_limitation_assertion(segment):
                    paragraph_segments.extend(_split_mixed_followup_assertion(part))
        if paragraph_segments:
            # A cited run may be followed by an uncited limitation in the same
            # paragraph. Bind each run backward to its own closing citation;
            # do not borrow an earlier citation for the uncited trailing text.
            run_start = 0
            for position, segment in enumerate(paragraph_segments):
                citations = _CITATION_PATTERN.findall(segment)
                if not citations:
                    continue
                inherited = " ".join(f"[{value}]" for value in dict.fromkeys(citations))
                for pending_index in range(run_start, position):
                    paragraph_segments[pending_index] += f" {inherited}"
                run_start = position + 1
            segments.extend(
                _AnswerSegment(item, block_id=block_id, block_kind=block_kind)
                for item in paragraph_segments
            )
    return _inherit_bounded_block_citations(segments)


def _starts_markdown_list_item(text: str) -> bool:
    return bool(regex.match(r"^\s*(?:[-*+]|\p{Number}+[.)])\s+\S", text))


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
            same_owner = (
                getattr(segment, "block_id", None)
                == getattr(segments[before], "block_id", None)
                == getattr(segments[after], "block_id", None)
            )
            if same_owner or _CALCULATION_OPERATOR_PATTERN.search(_plain_claim_text(segment)):
                shared = set(_CITATION_PATTERN.findall(segments[before])) & set(
                    _CITATION_PATTERN.findall(segments[after])
                )
        if not shared:
            neighbor = _nearest_matching_cited_calculation(segments, index)
            if neighbor is None:
                neighbor = _nearest_cited_rate_for_derived_result(segments, index)
            if neighbor is None:
                continue
            shared = set(_CITATION_PATTERN.findall(segments[neighbor]))
        if shared:
            citations = " ".join(f"[{value}]" for value in sorted(shared))
            inherited[index] = _AnswerSegment(
                f"{segment} {citations}",
                block_id=getattr(segment, "block_id", index),
                block_kind=getattr(segment, "block_kind", "paragraph"),
            )
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


def _nearest_cited_rate_for_derived_result(segments: list[str], index: int) -> int | None:
    """Inherit from a cited rate when this claim is that rate applied to a setup amount."""
    parsed = _parse_calculation(segments[index])
    claim_amounts = _money_amounts(segments[index])
    if parsed is None and not claim_amounts:
        return None
    setup_amounts = _setup_amounts(segments)
    bases = tuple(dict.fromkeys((*claim_amounts, *setup_amounts)))
    for direction in (-1, 1):
        if _uncited_factual_in_direction(segments, index, -direction):
            continue
        neighbor = _nearest_cited_factual_segment(segments, index, direction=direction)
        if neighbor is None:
            continue
        neighbor_rates = _rates_in_evidence([segments[neighbor]])
        if not neighbor_rates:
            continue
        if parsed is not None:
            _base, rate, _result = parsed
            if any(abs(rate - item) <= _amount_tolerance(item) for item in neighbor_rates):
                return neighbor
            continue
        if any(
            _arithmetic_matches(base, rate, result)
            for rate in neighbor_rates
            for base in bases
            for result in claim_amounts
            if abs(base - result) > _amount_tolerance(result)
        ):
            return neighbor
    return None


def _uncited_factual_in_direction(segments: list[str], index: int, direction: int) -> bool:
    cursor = index + direction
    while 0 <= cursor < len(segments):
        candidate = segments[cursor]
        if _CITATION_PATTERN.search(candidate):
            return False
        if _is_non_factual_segment(candidate) or (
            _is_markdown_list_item(candidate) and _is_quantity_setup_segment(candidate)
        ):
            cursor += direction
            continue
        if _is_markdown_list_item(candidate):
            cursor += direction
            continue
        return not _is_non_factual_segment(candidate)
    return False


def _setup_amounts(segments: list[str]) -> tuple[Decimal, ...]:
    amounts: list[Decimal] = []
    for segment in segments:
        if _is_quantity_setup_segment(segment):
            amounts.extend(sorted(_money_amounts(segment)))
    return tuple(dict.fromkeys(amounts))


def _restates_cited_calculation(
    amounts: set[Decimal],
    base: Decimal,
    rate: Decimal,
    result: Decimal,
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
        _is_structural_segment(_plain_claim_text(text) or text)
        or _is_quantity_setup_segment(text)
        or _is_short_stance_segment(text)
        or _is_insufficiency_statement(text)
        or _is_evidence_followup_statement(text)
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
        or _POLARITY_PATTERN.fullmatch(stripped)
    )


def _fold_indic_digits(text: str) -> str:
    return text.translate(_BENGALI_DIGIT_FOLD)


def _proposal_quantity_role_conflict(assertion: str, sources: list[str]) -> bool:
    if not _currency_amounts(assertion):
        return False
    proof = " ".join(sources).casefold()
    claim = assertion.casefold()
    if "tax-free" not in proof and "tax free" not in proof:
        return False
    roles = ("filing fee", "penalty", "rebate", "deduction certificate")
    categories = ("women", "senior citizens", "third-gender", "freedom fighters")
    return any(role in claim and role not in proof for role in (*roles, *categories))


def _bound_period_amount_conflict(assertion: str, sources: list[str]) -> bool:
    """Keep a flattened table's complete period cell bound to its following amount."""

    def fold(text: str) -> str:
        return text.translate(_BENGALI_DIGIT_FOLD).replace("\u2013", "-").replace("\u2014", "-")

    period_pattern = regex.compile(r"(?<!\d)(20\d{2})-(20\d{2}|\d{2})(?!\d)")

    def periods(text: str) -> set[str]:
        return {
            a + "-" + (b if len(b) == 4 else a[:2] + b)
            for a, b in period_pattern.findall(fold(text))
        }

    def canonical(text: str) -> str:
        return " ".join(_plain_claim_text(text).split()).casefold()

    if any(canonical(assertion) == canonical(text) for text in sources) or (
        canonical(assertion) == canonical(" ".join(sources))
    ):
        return False
    requested = periods(assertion)
    amounts = _currency_amounts(assertion)
    if not requested or not amounts:
        return False
    bindings: dict[str, set[Decimal]] = {}
    for source_text in sources:
        active: set[str] = set()
        for line in fold(source_text).splitlines():
            labels = periods(line)
            if labels:
                active = labels
            values = _currency_amounts(line)
            if active and values:
                for label in active:
                    bindings.setdefault(label, set()).update(values)
                active = set()
    return any(label in bindings and not amounts.issubset(bindings[label]) for label in requested)


def _plain_claim_text(text: str) -> str:
    stripped = _CITATION_PATTERN.sub("", text).replace(_TABLE_HEADER_SENTINEL, "")
    stripped = stripped.removeprefix("[echo] ").strip()
    return regex.sub(r"[*_`]+", "", stripped).strip()


def _is_markdown_list_item(text: str) -> bool:
    return bool(_MARKDOWN_LIST_ITEM_PATTERN.match(_plain_claim_text(text)))


def _is_quantity_setup_segment(text: str) -> bool:
    """Treat uncited labeled calculation inputs as setup, not corpus claims.

    A cited labeled quantity such as a documented threshold remains a verifiable
    factual claim. Uncited ``Eligible investment: BDT 60,000`` stays setup.
    """
    if _CITATION_PATTERN.search(text):
        return False
    folded = _fold_indic_digits(_plain_claim_text(text))
    if _CALCULATION_OPERATOR_PATTERN.search(folded):
        return False
    return bool(
        _QUANTITY_SETUP_PATTERN.fullmatch(folded) or _SCENARIO_INPUT_PATTERN.fullmatch(folded)
    )


def _claim_kind(text: str, user_input: str, *, display: str | None = None) -> str:
    """Inspection categories do not exempt any assertion from source verification."""
    if _coverage_kind_hint(text, display=display or text) == "coverage_scope":
        return "coverage_scope"
    plain = _plain_claim_text(text).casefold()
    if _CALCULATION_OPERATOR_PATTERN.search(plain):
        return "arithmetic"
    normalized = " ".join(plain.split()).strip(" .")
    if normalized and normalized in " ".join(user_input.casefold().split()):
        return "scenario_input"
    table_cells = [cell.strip() for cell in plain.strip("|").split("|")]
    if (
        len(table_cells) == 2
        and regex.search(r"\b(?:supplied|provided|declared|your)\b", table_cells[0])
        and regex.fullmatch(rf"{_CURRENCY_TOKEN}(?:{_NUMBER})", table_cells[1])
        and _amount_set(table_cells[1]).issubset(_amount_set(user_input))
    ):
        return "scenario_input"
    if regex.search(r"\b(?:assum(?:e|ed|ing|ption)|suppos(?:e|ing))\b|ধরে|অনুমান", plain):
        return "assumption"
    if regex.search(r"\b(?:please provide|if you (?:provide|share)|to refine)\b", plain):
        return "refinement"
    return "source_assertion"


def _arithmetic_consistency(text: str) -> ClaimVerification | None:
    """Check explicit simple equations only; correctness never proves a legal rule."""
    plain = _fold_indic_digits(_plain_claim_text(text))
    if "=" not in plain and "equals" not in plain:
        return None
    results: list[bool] = []
    for pattern in _CALCULATION_PATTERNS:
        for match in pattern.finditer(plain):
            base, rate, result = (
                Decimal(match.group(key).replace(",", "")) for key in ("base", "rate", "result")
            )
            results.append(abs(base * rate / 100 - result) <= _amount_tolerance(result))
    binary = regex.compile(
        rf"(?<![\d,.])(?P<left>{_NUMBER})\s*(?P<op>[+\u2212-])\s*"
        rf"{_CURRENCY_TOKEN}(?P<right>{_NUMBER})\s*=\s*"
        rf"{_CURRENCY_TOKEN}(?P<result>{_NUMBER})(?!\d|[,.]\d)"
    )
    for match in binary.finditer(plain):
        # Do not certify a suffix of a longer or parenthesized expression.
        prefix = plain[: match.start()].rstrip()
        if prefix and prefix[-1] in "+-\u2212*/(":
            continue
        left, right, result = (
            Decimal(match.group(key).replace(",", "")) for key in ("left", "right", "result")
        )
        expected = left + right if match.group("op") == "+" else left - right
        results.append(abs(expected - result) <= _amount_tolerance(result))
    if not results:
        return ClaimVerification.UNVERIFIED
    if not all(results):
        return ClaimVerification.UNSUPPORTED
    # Multiple equal signs require every equation to be recognized.
    if len(results) < plain.count("="):
        return ClaimVerification.UNVERIFIED
    return ClaimVerification.SUPPORTED


def _parse_amount(value: str) -> Decimal:
    return Decimal(value.replace(",", "").replace("٬", ""))


def _amount_tolerance(value: Decimal) -> Decimal:
    # A displayed whole unit may round by 0.5; two decimals by 0.005.
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("Cannot round a non-finite amount")
    return Decimal("0.5").scaleb(min(exponent, 0))


def _arithmetic_matches(base: Decimal, rate: Decimal, result: Decimal) -> bool:
    expected = base * rate / Decimal(100)
    return abs(expected - result) <= _amount_tolerance(result)


def _amount_set(text: str) -> set[Decimal]:
    """Decimal quantities retain grouped decimals and their original spans."""
    return {item.value for item in normalize_quantities(_plain_claim_text(text))}


def _money_amounts(text: str) -> set[Decimal]:
    """Use typed monetary values, plus complete spelled currency expressions."""
    return {item.value for item in _numeric_money_quantities(text, allow_untyped=True)} | (
        _currency_amounts(text)
    )


def _evidence_money_amounts(text: str, *, claim: str = "") -> set[Decimal]:
    """Only monetary evidence with a compatible locally stated amount role."""
    text = regex.sub(r"\[footnote\s+\d+\]", "", text, flags=regex.IGNORECASE)
    claim_roles = {
        role
        for item in _numeric_money_quantities(claim)
        if (role := _money_role(item.role)) is not None
    }
    return {
        item.value
        for item in _numeric_money_quantities(text)
        if not claim_roles or (role := _money_role(item.role)) is None or role in claim_roles
    } | _spelled_currency_amounts(text)


def _money_role(role: str | None) -> str | None:
    """Normalize only distinctive monetary subjects; generic units stay neutral."""
    if role in {"fine", "penalty", "জরিমানা"}:
        return "fine"
    if role in {"fee", "price", "cost"}:
        return "fee"
    if role in {"income", "salary", "আয়", "আয়"}:
        return "income"
    if role in {"rebate", "রেয়াত", "রেয়াত"}:
        return "rebate"
    return None


def _numeric_money_quantities(text: str, *, allow_untyped: bool = False) -> tuple[Quantity, ...]:
    """Keep numeric roles and reject a conflicting parenthetical spelling."""
    words = _quantity_number_words()
    for word, number in list(words.items()):
        if word.isascii() and 1 <= number <= 9:
            words[f"{word} hundred"] = number * 100
    values: list[Quantity] = []
    for item in normalize_quantities(text):
        if not (
            (item.kind == "money" and (item.explicit_currency or item.value >= 100))
            or (allow_untyped and item.kind == "number" and item.value >= 100)
        ):
            continue
        spelling = regex.match(r"\s*\((?P<words>[\p{L}\p{M} -]+)\)", text[item.end :])
        if spelling and _parenthetical_number_value(spelling.group("words"), words) != item.value:
            continue
        values.append(item)
    return tuple(values)


def _currency_amounts(text: str) -> set[Decimal]:
    return {
        item.value for item in _numeric_money_quantities(text) if item.explicit_currency
    } | _spelled_currency_amounts(text)


def _spelled_currency_amounts(text: str) -> set[Decimal]:
    folded = _fold_indic_digits(text)
    currency = r"(?:\b(?:BDT|Tk|taka)\b|৳|টাকা(?:র)?)"
    values: set[Decimal] = set()
    # Only complete currency expressions qualify. Do not pull a small component
    # out of a compound amount (e.g. "one hundred fifty taka"). Semantic claim
    # verification still establishes what the amount applies to.
    words = _quantity_number_words()
    words.pop("বার", None)
    for word, number in list(words.items()):
        if 1 <= number <= 9:
            if word.isascii():
                words[f"{word} hundred"] = number * 100
            else:
                for suffix in ("শত", "শো"):
                    words[word + suffix] = number * 100
                    words[word + " " + suffix] = number * 100
    # Bengali statutes also spell composite hundreds and scaled amounts.
    # Match the complete expression, never just its trailing small component.
    small_words = [
        (word, value) for word, value in words.items() if not word.isascii() and value < 100
    ]
    for hundred, value in list(words.items()):
        if not hundred.isascii() and value >= 100:
            for tail, remainder in small_words:
                words[f"{hundred} {tail}"] = value + remainder
    scales = {
        "hundred": 100,
        "thousand": 1000,
        "million": 1000000,
        "billion": 1000000000,
        "lakh": 100000,
        "lac": 100000,
        "crore": 10000000,
        "শত": 100,
        "হাজার": 1000,
        "লক্ষ": 100000,
        "লাখ": 100000,
        "কোটি": 10000000,
    }
    scale_pattern = "|".join(scales)
    numeric_with_spelling = (
        rf"(?P<number>{_NUMBER})\s*\((?P<spelling>[\p{{L}}\p{{M}} -]+)\)"
        rf"\s*\]?(?:\s*(?P<scale>{scale_pattern})(?![\p{{L}}\p{{M}}]))?"
    )
    # The typed parser cannot see through a parenthetical spelling between the
    # digits and currency unit. Admit that narrow legacy form only when it agrees.
    for pattern in (
        rf"{currency}\s*{numeric_with_spelling}",
        rf"{numeric_with_spelling}\s*{currency}",
    ):
        for match in regex.finditer(pattern, folded, regex.IGNORECASE):
            amount = _parse_amount(match.group("number"))
            if _parenthetical_number_value(match.group("spelling"), words) != amount:
                continue
            values.add(amount * scales.get((match.group("scale") or "").casefold(), 1))
    alternatives = "|".join(regex.escape(word) for word in sorted(words, key=len, reverse=True))
    for match in regex.finditer(
        rf"(?<![\p{{L}}\p{{M}}\d])(?P<words>{alternatives})"
        rf"(?:\s+(?P<scale>{scale_pattern}))?\s+{currency}",
        folded,
        regex.IGNORECASE,
    ):
        before = folded[: match.start()].rstrip()
        # A preceding numeric/scaled component makes this an unsupported compound.
        if regex.search(
            r"(?:\d|hundred|thousand|million|billion|lakh|crore|শত|শো|হাজার|লক্ষ|লাখ|কোটি)"
            r"(?:\s+and)?$",
            before,
            regex.IGNORECASE,
        ):
            continue
        values.add(
            Decimal(words[match.group("words").lower()])
            * scales.get((match.group("scale") or "").lower(), 1)
        )
    return values


def _amounts_include(amounts: set[Decimal], value: Decimal) -> bool:
    tolerance = _amount_tolerance(value)
    return any(abs(value - other) <= tolerance for other in amounts)


def _parse_calculation(text: str) -> tuple[Decimal, Decimal, Decimal] | None:
    folded = _fold_indic_digits(_plain_claim_text(text))
    for pattern in _CALCULATION_PATTERNS:
        match = pattern.search(folded)
        if match is None:
            continue
        return (
            _parse_amount(match.group("base")),
            _parse_amount(match.group("rate")),
            _parse_amount(match.group("result")),
        )
    return None


def _rate_in_evidence(rate: Decimal, evidence_texts: list[str]) -> bool:
    folded_evidence = _fold_indic_digits(" ".join(evidence_texts))
    rendered = f"{rate:g}"
    markers = (f"{rendered}%", f"{rendered} %")
    return any(marker in folded_evidence for marker in markers)


def _rates_in_evidence(evidence_texts: list[str]) -> tuple[Decimal, ...]:
    folded_evidence = _fold_indic_digits(" ".join(evidence_texts))
    found = (_parse_amount(match) for match in _EVIDENCE_RATE_PATTERN.findall(folded_evidence))
    return tuple(dict.fromkeys(found))


def _exceeds_explicit_band(base: Decimal, rate: Decimal, evidence_texts: list[str]) -> bool:
    """Reject a per-band calculation exceeding an explicit tabular 'next' width.

    This is a narrow contradiction check, not a tax parser or applicability proof.
    Unknown table syntax remains subject to the normal evidence checks.
    """
    widths = [
        _parse_amount(match.group("width"))
        for source in evidence_texts
        for match in _BAND_WIDTH_PATTERN.finditer(_fold_indic_digits(source))
        if _parse_amount(match.group("rate")) == rate
    ]
    return bool(widths) and base > max(widths)


def _derived_calculation_verification(
    text: str,
    evidence_texts: list[str],
    *,
    adjacent_texts: tuple[str, ...] = (),
    extra_bases: tuple[Decimal, ...] = (),
    authorized_operands: set[Decimal] | None = None,
) -> ClaimVerification | None:
    """Verify rate x amount arithmetic against cited evidence.

    User-supplied operands are not required to appear in the corpus. The cited
    rate must, and the arithmetic must evaluate. A conclusion that restates
    the result of an adjacent cited calculation is verified the same way.
    """
    # Check every equation, not just the first correct equation in a sentence.
    calculations = [
        tuple(_parse_amount(match.group(key)) for key in ("base", "rate", "result"))
        for pattern in _CALCULATION_PATTERNS
        for match in pattern.finditer(_fold_indic_digits(_plain_claim_text(text)))
    ]
    if calculations:
        for base, rate, result in calculations:
            if (
                not _arithmetic_matches(base, rate, result)
                or (authorized_operands is not None and base not in authorized_operands)
                or not _rate_in_evidence(rate, evidence_texts)
                or _exceeds_explicit_band(base, rate, evidence_texts)
            ):
                return ClaimVerification.UNSUPPORTED
        return ClaimVerification.SUPPORTED
    pair = _derived_amount_pair_verification(
        text, evidence_texts, extra_bases=extra_bases, authorized_operands=authorized_operands
    )
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
        if _rate_in_evidence(rate, evidence_texts) and not _exceeds_explicit_band(
            base, rate, evidence_texts
        ):
            return ClaimVerification.SUPPORTED
    if matched:
        return ClaimVerification.UNSUPPORTED
    return None


def _derived_amount_pair_verification(
    text: str,
    evidence_texts: list[str],
    *,
    extra_bases: tuple[Decimal, ...] = (),
    authorized_operands: set[Decimal] | None = None,
) -> ClaimVerification | None:
    claim_amounts = _money_amounts(text)
    if not claim_amounts:
        return None
    rates = _rates_in_evidence(evidence_texts)
    if not rates:
        return None
    bases = tuple(dict.fromkeys((*claim_amounts, *extra_bases)))
    for base in bases:
        if authorized_operands is not None and base not in authorized_operands:
            continue
        for result in claim_amounts:
            if abs(base - result) <= _amount_tolerance(result):
                continue
            for rate in rates:
                if _arithmetic_matches(base, rate, result):
                    if _exceeds_explicit_band(base, rate, evidence_texts):
                        return ClaimVerification.UNSUPPORTED
                    return ClaimVerification.SUPPORTED
    return None


def _is_insufficiency_statement(text: str) -> bool:
    """A bare refusal without a coverage verdict is not a verifiable claim."""
    return _INSUFFICIENCY_MARKER in _plain_claim_text(text).casefold()


def _is_evidence_followup_statement(text: str) -> bool:
    """Operational verification advice is not a factual corpus assertion."""
    plain = _plain_claim_text(text).strip()
    if regex.search(
        r"(?:,|;)\s*(?:therefore|so|thus|consequently)\b|"
        r"\b(?:is|are)\s+(?:unnecessary|not required|exempt)\b|"
        r"\b(?:exempt|exemption|need not|does not need to)\b|"
        r"\b(?:must|shall)\s+(?:not\s+)?(?:register|file|pay|hold)\b",
        plain,
        regex.IGNORECASE,
    ):
        return False
    return bool(_EVIDENCE_FOLLOWUP_PATTERN.fullmatch(plain))


def _coverage_kind_hint(text: str, *, display: str | None = None) -> str | None:
    material = _plain_claim_text(text)
    shown = _plain_claim_text(display or text)
    if _WHOLE_CORPUS_ABSENCE_PATTERN.search(material) or _WHOLE_CORPUS_ABSENCE_PATTERN.search(
        shown
    ):
        return "coverage_scope"
    if not (_COVERAGE_SCOPE_PATTERN.search(material) or _COVERAGE_SCOPE_PATTERN.search(shown)):
        return None
    if regex.search(
        r"(?:,|;)\s*(?:so|therefore|thus|consequently)\b|(?:তাই|অতএব|সুতরাং)",
        shown,
        regex.IGNORECASE,
    ):
        # The limitation does not exempt a conclusion joined to it.
        return None
    if _COVERAGE_SCOPE_PATTERN.search(shown) or _WHOLE_CORPUS_ABSENCE_PATTERN.search(shown):
        return "coverage_scope"
    if _DUTY_MARKER_PATTERN.search(shown):
        # A scope heading or inherited preamble cannot exempt a statutory duty.
        return None
    return "coverage_scope"


def _is_bounded_coverage_continuation(segments: list[str], index: int, display: str) -> bool:
    """Recognize meta-level continuation only inside one limitation paragraph.

    Pronouns such as ``They`` are never sufficient by themselves.  The preceding
    sentence must belong to the same Markdown block and already be an explicit
    evidence-coverage limitation.  This keeps a legal conclusion in another
    paragraph or list item fully verifiable.
    """
    plain = _plain_claim_text(display).strip()
    if regex.search(r"\b(?:exempt|exemption|need not|does not need to)\b", plain, regex.IGNORECASE):
        return False
    source_antecedent = regex.search(
        r"^it does not(?:,? by itself,?)? establish\b", plain, regex.IGNORECASE
    )
    if not (
        _COVERAGE_CONTINUATION_PATTERN.search(plain)
        or _COVERAGE_SUMMARY_PATTERN.search(plain)
        or source_antecedent
    ):
        return False
    if index <= 0:
        return False
    current = segments[index]
    previous = segments[index - 1]
    if getattr(current, "block_id", None) != getattr(previous, "block_id", None):
        return False
    previous_plain = _plain_claim_text(previous)
    if source_antecedent:
        return bool(
            regex.search(
                r"\b(?:that|this|the) (?:provision|source|passage|evidence|rule)\b",
                previous_plain,
                regex.IGNORECASE,
            )
        )
    return bool(
        _COVERAGE_SCOPE_PATTERN.search(previous_plain)
        or _COVERAGE_CONTINUATION_PATTERN.search(previous_plain)
    )


def _coverage_scope_verification(
    text: str,
    coverage: dict[str, Any] | None,
    *,
    display: str | None = None,
) -> tuple[ClaimVerification, str]:
    """Validate limitation prose against the trusted coverage verdict, not the corpus."""
    shown = display or text
    coverage = _normalize_coverage(coverage)
    if _WHOLE_CORPUS_ABSENCE_PATTERN.search(_plain_claim_text(text)) or (
        _WHOLE_CORPUS_ABSENCE_PATTERN.search(_plain_claim_text(shown))
    ):
        return (
            ClaimVerification.UNSUPPORTED,
            ClaimVerificationReason.WHOLE_CORPUS_ABSENCE_UNPROVEN,
        )
    if not coverage:
        return (
            ClaimVerification.UNVERIFIED,
            ClaimVerificationReason.COVERAGE_VERDICT_UNAVAILABLE,
        )
    if coverage.get("admitted_evidence_fallback") is True and regex.fullmatch(
        r"This is a limited source-based overview; complete coverage has not been verified\.?",
        _plain_claim_text(text).strip(),
        flags=regex.IGNORECASE,
    ):
        # This is a known execution limitation, not an assertion that a legal
        # duty is absent. Specific missing-topic claims still require review.
        return ClaimVerification.SUPPORTED, ClaimVerificationReason.MATCHES_COVERAGE_VERDICT
    if (
        coverage.get("partial_scope_validated") is False
        and coverage.get("full_coverage_validated") is not True
    ):
        return (
            ClaimVerification.UNVERIFIED,
            ClaimVerificationReason.COVERAGE_VERDICT_UNAVAILABLE,
        )
    if _coverage_topic_supported(text, coverage) or _coverage_topic_supported(shown, coverage):
        return ClaimVerification.SUPPORTED, ClaimVerificationReason.MATCHES_COVERAGE_VERDICT
    if _COVERAGE_SUMMARY_PATTERN.search(_plain_claim_text(shown)) and (
        coverage.get("partial_scope_validated") is True
        or coverage.get("full_coverage_validated") is True
    ):
        return ClaimVerification.SUPPORTED, ClaimVerificationReason.MATCHES_COVERAGE_VERDICT
    if regex.search(
        r"cannot responsibly state from (?:these|the) materials\b",
        _plain_claim_text(shown),
        regex.IGNORECASE,
    ) and (coverage.get("partial_scope_validated") is True or _coverage_topics(coverage)):
        # This sentence refers back to the immediately preceding enumerated
        # coverage gap. It is a limitation, not a denial of the underlying duty.
        return ClaimVerification.SUPPORTED, ClaimVerificationReason.MATCHES_COVERAGE_VERDICT
    if _COVERAGE_SCOPE_PATTERN.search(_plain_claim_text(shown)) or _COVERAGE_SCOPE_PATTERN.search(
        _plain_claim_text(text)
    ):
        if _coverage_topics(coverage):
            return (
                ClaimVerification.UNVERIFIED,
                ClaimVerificationReason.COVERAGE_TOPIC_NOT_MATCHED,
            )
        return (
            ClaimVerification.UNVERIFIED,
            ClaimVerificationReason.COVERAGE_VERDICT_UNAVAILABLE,
        )
    return (
        ClaimVerification.UNSUPPORTED,
        ClaimVerificationReason.COVERAGE_STATEMENT_NOT_IN_VERDICT,
    )


def _normalize_coverage(coverage: dict[str, Any] | None) -> dict[str, Any]:
    """Accept either a flat coverage dict or the knowledge_repair diagnostics envelope."""
    if not coverage:
        return {}
    nested = coverage.get("coverage")
    partial = coverage.get("partial_answer")
    if isinstance(nested, dict):
        return {
            "missing": list(nested.get("missing") or []),
            "missing_inputs": list(nested.get("missing_inputs") or []),
            "partial_answer": (
                partial if isinstance(partial, dict) else nested.get("partial_answer")
            ),
            "full_coverage_validated": nested.get("full_coverage_validated"),
            "partial_scope_validated": nested.get("partial_scope_validated"),
            "admitted_evidence_fallback": nested.get("admitted_evidence_fallback"),
        }
    inherited_partial = coverage.get("coverage_partial") is True or nested == "partial"
    if inherited_partial or isinstance(partial, dict):
        pending = []
        if isinstance(partial, dict):
            pending = list(partial.get("pending") or partial.get("exclusions") or [])
        return {
            "missing": pending,
            "missing_inputs": list(coverage.get("missing_inputs") or []),
            "partial_answer": partial if isinstance(partial, dict) else None,
            "full_coverage_validated": False,
            "partial_scope_validated": True,
        }
    return coverage


def _coverage_topics(coverage: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    for key in ("missing", "missing_inputs"):
        value = coverage.get(key)
        if isinstance(value, list):
            topics.extend(str(item) for item in value if str(item).strip())
    partial = coverage.get("partial_answer")
    if isinstance(partial, dict):
        for key in ("exclusions", "pending"):
            value = partial.get(key)
            if isinstance(value, list):
                topics.extend(str(item) for item in value if str(item).strip())
    return topics


def _coverage_topic_supported(text: str, coverage: dict[str, Any]) -> bool:
    folded = _plain_claim_text(text).casefold()
    text_concepts = _coverage_concepts(text)
    text_facets = _coverage_facets(text)
    for topic in _coverage_topics(coverage):
        candidate = topic.casefold().strip()
        if not candidate:
            continue
        topic_concepts = _coverage_concepts(topic)
        topic_facets = _coverage_facets(topic)
        # Sharing a subject is insufficient when both labels name different
        # facets. A missing fee cannot validate a deadline or filing duty.
        if (
            text_concepts & topic_concepts
            and text_facets
            and topic_facets
            and text_facets.isdisjoint(topic_facets)
        ):
            continue
        if candidate in folded or folded in candidate:
            return True
        if _coverage(_significant_tokens(topic), _significant_tokens(text)) >= 0.4:
            return True
        if topic_concepts & text_concepts:
            return True
    return False


def _coverage_concepts(text: str) -> set[str]:
    """Bilingual identities for recurring requirement labels, never factual proof."""
    folded = _plain_claim_text(text).casefold()
    aliases = {
        "annual_return": ("annual return", "annual list", "বার্ষিক রিটার্ন", "বার্ষিক তালিকা"),
        "tax_liability": (
            "tax payable",
            "taxable income",
            "tax liability",
            "liability without operations or income",
        ),
        "auditor_appointment": (
            "auditor appointment",
            "appointment of auditor",
            "নিরীক্ষক নিয়োগ",
            "নিরীক্ষক নিয়োগ",
        ),
    }
    return {
        concept for concept, values in aliases.items() if any(value in folded for value in values)
    }


def _coverage_facets(text: str) -> set[str]:
    """Distinguish common missing facets without treating these labels as proof."""
    folded = _plain_claim_text(text).casefold()
    aliases = {
        "duty": (
            "duty",
            "obligation",
            "required to file",
            "দায়িত্ব",
            "দায়িত্ব",
            "বাধ্যবাধকতা",
        ),
        "deadline": ("deadline", "time limit", "within", "সময়সীমা", "সময়সীমা", "দিনের মধ্যে"),
        "fee": ("fee", "fees", "charge", "cost", "ফি", "খরচ"),
        "procedure": ("procedure", "process", "how to file", "পদ্ধতি", "প্রক্রিয়া", "প্রক্রিয়া"),
        "penalty": ("penalty", "sanction", "fine", "জরিমানা", "দণ্ড"),
        "applicability": ("applicability", "applies to", "scope", "প্রযোজ্য", "প্রযোজ্যতা"),
    }
    return {facet for facet, values in aliases.items() if any(value in folded for value in values)}


def _is_markdown_table_row(text: str) -> bool:
    stripped = _plain_claim_text(text).strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_leading_table_header(segments: list[str], index: int) -> bool:
    return 0 <= index < len(segments) and segments[index].startswith(_TABLE_HEADER_SENTINEL)


def _verification_context(segments: list[str], index: int) -> str:
    parts: list[str] = []
    current = segments[index] if 0 <= index < len(segments) else ""
    if _is_markdown_table_row(current):
        start = index
        while start > 0 and _is_markdown_table_row(segments[start - 1]):
            start -= 1
        if start != index:
            parts.append(_plain_claim_text(segments[start]))
    for prev in range(index - 1, -1, -1):
        plain = _plain_claim_text(segments[prev]).strip()
        if _MARKDOWN_HEADING_PATTERN.fullmatch(plain) or _LIST_PREAMBLE_PATTERN.fullmatch(plain):
            parts.append(plain)
            break
        if _is_markdown_table_row(segments[prev]) or _is_structural_segment(plain):
            continue
        if _is_markdown_list_item(segments[prev]):
            continue
        break
    return " ".join(reversed(parts))


def _contextualized_assertion(display: str, context: str) -> str:
    plain = _plain_claim_text(display)
    if not context or context.casefold() in plain.casefold():
        return plain
    return f"{context} {plain}"


def _claim_candidate_spans(chunk: ContextChunk) -> list[_SelectedSpan]:
    """Keep headings as context, not a standalone witness for a body claim."""
    spans = _candidate_spans(chunk.content)
    headings = chunk.metadata.get("heading_path")
    if not isinstance(headings, list):
        return spans
    labels = {str(label).strip() for label in headings if str(label).strip()}
    body = [span for span in spans if span.derivation != "chunk" and span.text not in labels]
    if not body:
        return spans
    # The complete passage retains governing headings, conditions and exact offsets.
    # Only isolated metadata headings are excluded from locator competition.
    return [span for span in spans if span.derivation == "chunk" or span.text not in labels]


def _candidate_spans(content: str) -> list[_SelectedSpan]:
    spans: list[_SelectedSpan] = []
    last = 0
    for match in _SPAN_BOUNDARY_PATTERN.finditer(content):
        piece = content[last : match.start()]
        text = piece.strip()
        if text:
            start = last + piece.find(text)
            spans.append(
                _SelectedSpan(
                    text=text,
                    char_start=start,
                    char_end=start + len(text),
                    derivation="sentence",
                    semantic_score=None,
                    semantic_span_aligned=False,
                )
            )
        last = match.end()
    piece = content[last:]
    text = piece.strip()
    if text:
        start = last + piece.find(text)
        spans.append(
            _SelectedSpan(
                text=text,
                char_start=start,
                char_end=start + len(text),
                derivation="sentence",
                semantic_score=None,
                semantic_span_aligned=False,
            )
        )
    if content.strip():
        stripped = content.strip()
        start = content.find(stripped)
        full = _SelectedSpan(
            text=stripped,
            char_start=max(start, 0),
            char_end=max(start, 0) + len(stripped),
            derivation="chunk",
            semantic_score=None,
            semantic_span_aligned=False,
        )
        if not spans or spans[0].text != stripped:
            spans.append(full)
    return spans


def _digit_tokens(text: str) -> set[str]:
    return set(regex.findall(r"\d+", _fold_indic_digits(text)))


def _sample_spans(spans: list[_SelectedSpan], limit: int) -> list[_SelectedSpan]:
    if len(spans) <= limit:
        return list(spans)
    head, tail = min(4, limit // 2), min(4, limit - min(4, limit // 2))
    middle = spans[head : len(spans) - tail] if tail else spans[head:]
    budget = max(0, limit - head - tail)
    sampled: list[_SelectedSpan] = []
    if budget and middle:
        step = max(len(middle) / budget, 1)
        sampled = [middle[min(int(index * step), len(middle) - 1)] for index in range(budget)]
    tail_spans = spans[-tail:] if tail else []
    chosen = [*spans[:head], *sampled, *tail_spans]
    seen: set[tuple[int, int]] = set()
    unique: list[_SelectedSpan] = []
    for span in chosen:
        key = (span.char_start, span.char_end)
        if key in seen:
            continue
        seen.add(key)
        unique.append(span)
    return unique


def _prefilter_spans(
    spans: list[_SelectedSpan],
    assertion: str,
    display: str,
    *,
    limit: int,
) -> list[_SelectedSpan]:
    sentences = [span for span in spans if span.derivation != "chunk"]
    full = next((span for span in spans if span.derivation == "chunk"), None)
    claim_tokens = _significant_tokens(assertion) | _significant_tokens(display)
    digits = _digit_tokens(f"{assertion} {display}")

    def rank(span: _SelectedSpan) -> tuple[float, int]:
        return (
            _coverage(claim_tokens, _significant_tokens(span.text)),
            len(digits & _digit_tokens(span.text)),
        )

    scored = sorted(sentences, key=rank, reverse=True)
    chosen = (
        scored[:limit]
        if scored and rank(scored[0]) != (0.0, 0)
        else _sample_spans(sentences, limit)
    )
    if full is not None and all(span.text != full.text for span in chosen):
        chosen.append(full)
    return chosen


def _spans_for_embedding(
    assertion: str,
    display: str,
    chunk: ContextChunk,
    spans_by_chunk: dict[uuid.UUID, list[_SelectedSpan]],
) -> list[_SelectedSpan]:
    spans = spans_by_chunk.setdefault(chunk.chunk_id, _claim_candidate_spans(chunk))
    same_script = _uses_lexical_verification(assertion, [chunk.content])
    return _prefilter_spans(spans, assertion, display, limit=8 if same_script else 20)


def _best_evidence_spans(
    assertion: str,
    evidence_chunks: list[tuple[int, ContextChunk]],
    similarities: dict[tuple[str, str], float | None],
    spans_by_chunk: dict[uuid.UUID, list[_SelectedSpan]],
    *,
    display: str = "",
) -> dict[uuid.UUID, _SelectedSpan]:
    selected: dict[uuid.UUID, _SelectedSpan] = {}
    claim_tokens = _significant_tokens(assertion)
    keys = {assertion, display} - {""}
    for _, chunk in evidence_chunks:
        candidates = spans_by_chunk.get(chunk.chunk_id) or _claim_candidate_spans(chunk)
        if not candidates:
            continue

        def rank(span: _SelectedSpan) -> tuple[float, float, int]:
            score = _best_pair_score(similarities, keys, span.text)
            lexical = _coverage(claim_tokens, _significant_tokens(span.text))
            sentence = 1 if span.derivation != "chunk" else 0
            return (score if score is not None else -1.0, lexical, sentence)

        selected[chunk.chunk_id] = max(candidates, key=rank)
    return selected


def _best_pair_score(
    similarities: dict[tuple[str, str], float | None],
    keys: tuple[str, ...] | set[str],
    evidence: str,
) -> float | None:
    values = [
        score
        for key in keys
        if key
        for score in [similarities.get((key, evidence))]
        if score is not None
    ]
    return max(values) if values else None


def _durations_equivalent(left: tuple[int, str], right: tuple[int, str]) -> bool:
    left_number, left_unit = left
    right_number, right_unit = right
    if left_unit == right_unit:
        return left_number == right_number
    if {left_unit, right_unit} <= {"day", "week"}:
        left_days = left_number * (7 if left_unit == "week" else 1)
        right_days = right_number * (7 if right_unit == "week" else 1)
        return left_days == right_days
    return False


def _explained_quantity_values(claim: str, evidence: str) -> set[Decimal]:
    """Numbers in the claim that evidence already accounts for, including equivalent durations."""
    values = set(_amount_set(evidence))
    values.update(Decimal(value) for value in _spelled_number_values(evidence))
    values.update(_currency_amounts(evidence))
    evidence_durations = _duration_quantities(evidence)
    values.update(Decimal(number) for number, _ in evidence_durations)
    for duration in _duration_quantities(claim):
        if any(_durations_equivalent(duration, other) for other in evidence_durations):
            values.add(Decimal(duration[0]))
    return values


def _is_necessary_condition(text: str) -> bool:
    return bool(_NECESSARY_CONDITION_PATTERN.search(text))


def _is_required_for_validity(text: str) -> bool:
    if _is_bare_sufficient_condition(text):
        return False
    return bool(_REQUIRED_FOR_VALIDITY_PATTERN.search(text))


_CLAUSE_NEGATION = regex.compile(
    r"\b(?:no|not|never|cannot|can't|doesn't|does not|isn't|is not|without)\b|"
    r"(?<![\p{L}\p{M}])(?:না|নয়|নয়|নাই|ব্যতীত)(?![\p{L}\p{M}])",
    regex.IGNORECASE,
)


def _negated_term_anchors(text: str) -> frozenset[str]:
    """Return the first meaningful term governed by each explicit negation."""
    anchors: set[str] = set()
    for match in _CLAUSE_NEGATION.finditer(text):
        tail = text[match.end() :]
        tokens = regex.findall(r"[\p{L}\p{M}\p{N}]+", tail)
        for token in tokens:
            folded = token.casefold()
            if folded not in {"a", "an", "the", "is", "are", "be", "being", "to"}:
                anchors.add(folded)
                break
    return frozenset(anchors)


def _necessary_condition_polarity(
    text: str,
) -> tuple[bool, bool, frozenset[str]] | None:
    """Return bounded proposition, necessity, and condition negation structure.

    ``not P unless Q`` and ``P only if Q`` both encode positive ``P`` with
    necessary condition ``Q``.  Keeping the two clause polarities separate stops
    a negation moved from the proposition to the condition from looking aligned.
    """
    unless = regex.search(r"(?P<main>.+?)\bunless\b(?P<condition>.+)", text, regex.IGNORECASE)
    if unless is not None and _CLAUSE_NEGATION.search(unless.group("main")):
        # Remove the conventional negation in "not P unless Q". Any remaining
        # explicit negation still changes the main proposition's polarity.
        logical_main = _CLAUSE_NEGATION.sub(" ", unless.group("main"), count=1)
        return (
            bool(_CLAUSE_NEGATION.search(logical_main)),
            False,
            _negated_term_anchors(unless.group("condition")),
        )

    only = regex.search(
        r"(?P<main>.+?)\bonly\s+(?:if|when)\b(?P<condition>.+)",
        text,
        regex.IGNORECASE,
    )
    if only is not None:
        return (
            bool(_CLAUSE_NEGATION.search(only.group("main"))),
            False,
            _negated_term_anchors(only.group("condition")),
        )

    required = regex.search(
        r"(?P<condition>.+?)\b(?P<operator>is\s+not\s+required|is\s+required|"
        r"required|must\s+not|must)\b.{0,20}?\b(?:for|in order for)\b"
        r"(?P<main>.+?)\bto be valid\b",
        text,
        regex.IGNORECASE,
    )
    if required is not None:
        return (
            bool(_CLAUSE_NEGATION.search(required.group("main"))),
            bool(_CLAUSE_NEGATION.search(required.group("operator"))),
            _negated_term_anchors(required.group("condition")),
        )
    return None


def _suppress_negation_mismatch(claim: str, evidence: str) -> bool:
    """Keep aligned only-if/unless paraphrases; still reject polarity reversals."""
    claim_polarity = _necessary_condition_polarity(claim)
    evidence_polarity = _necessary_condition_polarity(evidence)
    if claim_polarity is not None and evidence_polarity is not None:
        return claim_polarity == evidence_polarity
    return False


def _section_190_polarity_scope(claim: str, evidence: str) -> tuple[str, str] | None:
    """Exclude Section 190 fallback conditions from the filing duty's polarity.

    The Bangla filing sentence contains negative *conditions* (no AGM and no
    listed officer) alongside a positive duty to file.  Compare those conditions
    only when the English claim actually states the corresponding fallback.
    """
    if not (
        "ব্যালান্স শীট" in evidence
        and regex.search(r"রেজিষ্ট্রারের\s+নিকট\s+দাখিল", evidence)
        and "ত্রিশদিন" in evidence
    ):
        return claim, evidence

    no_agm = regex.compile(r"বার্ষিক\s+সাধারণ\s+সভা\s+অনুষ্ঠিত\s+হয়\s+নাই")
    if no_agm.search(evidence):
        evidence = no_agm.sub("বার্ষিক সাধারণ সভা অনুষ্ঠিত হয়", evidence)
        claim = regex.sub(
            r"\bif\s+no\s+(?:annual\s+general\s+meeting|agm)\s+is\s+held\b",
            "if the AGM is held",
            claim,
            flags=regex.IGNORECASE,
        )

    no_officer = regex.compile(r"যদি\s+কোম্পানীতে\s+এইরূপ\s+পদধারী\s+কেহ\s+না\s+থাকেন")
    if no_officer.search(evidence):
        officer_fallback = regex.compile(
            r"\bif\s+(?:the\s+company\s+has\s+)?"
            r"(?:none\s+of\s+(?:those|the)|no)\s+officers?\b"
            r"(?=\s*(?:[,;.]|$))",
            regex.IGNORECASE,
        )
        officer_condition = regex.search(r"\bif\b[^.;,]{0,100}\bofficers?\b[^.;,]{0,100}", claim)
        if (
            "director" in claim
            and officer_condition
            and (
                _CLAUSE_NEGATION.search(officer_condition.group())
                or regex.search(r"\b(?:unwilling|unavailable)\b", officer_condition.group())
            )
            and not officer_fallback.search(claim)
        ):
            # Absence of office holders is not the same as their unwillingness,
            # availability, or another qualified condition. Do not let matching
            # negative words certify a condition this bounded grammar cannot compare.
            return None
        # An affirmative officer condition is not the statute's director fallback.
        if not ("director" in claim and officer_condition and not officer_fallback.search(claim)):
            evidence = no_officer.sub("যদি কোম্পানীতে এইরূপ পদধারী কেহ থাকেন", evidence)
            claim = officer_fallback.sub("if the company has officers", claim)
    return claim, evidence


def _is_bare_sufficient_condition(text: str) -> bool:
    if _is_necessary_condition(text):
        return False
    return bool(_BARE_SUFFICIENT_CONDITION_PATTERN.search(text))


def _bounded_entailment_guard(claim: str, evidence: str) -> ClaimVerification | None:
    """Reject a few high-risk contradictions that semantic similarity cannot resolve."""
    claim_plain = _plain_claim_text(claim).casefold()
    whole_evidence = _plain_claim_text(evidence).casefold()
    extension_claim = bool(regex.search(r"\b(?:extend|extension)\b|সময়\s+বৃদ্ধি", claim_plain))
    first_exclusion_pattern = (
        r"প্রথম\s+বার্ষিক\s+সাধারণ\s+সভা(?:র\s+ক্ষেত্র)?\s+ব্যতীত|"
        r"except\s+(?:for\s+)?the\s+first\s+(?:annual\s+general\s+meeting|agm)|"
        r"(?:not|other\s+than)\s+the\s+first\s+(?:annual\s+general\s+meeting|agm)"
    )
    # The exclusion may be in a different language from the answer. Scope it
    # to the extension clause before ordinary lexical clause alignment.
    extension_source = regex.search(
        rf"(?=[^।.]*{first_exclusion_pattern})[^।.]*", whole_evidence, regex.IGNORECASE
    )
    first_excluded = bool(extension_source and extension_claim)
    first_claim = bool(
        regex.search(
            r"\bfirst\s+(?:annual\s+general\s+meeting|agm)\b|প্রথম\s+বার্ষিক\s+সাধারণ\s+সভা",
            claim_plain,
        )
    )
    claim_excludes_first = bool(
        regex.search(
            r"\bfirst\s+(?:annual\s+general\s+meeting|agm)\s+"
            r"(?:(?:is|was)\s+(?:excluded|excepted|ineligible)|"
            r"(?:cannot|can't|may\s+not|must\s+not|does\s+not)\s+"
            r"(?:receive|obtain|qualify\s+for))\b|"
            r"\bextension\s+(?:does\s+not|cannot|may\s+not|must\s+not)\s+"
            r"apply\s+to\s+the\s+first\s+(?:annual\s+general\s+meeting|agm)\b|"
            r"\b(?:not|except|excluding)\s+the\s+first\s+"
            r"(?:annual\s+general\s+meeting|agm)\b",
            claim_plain,
        )
    )
    if first_excluded and first_claim and claim_excludes_first:
        return None
    if first_excluded and first_claim:
        return ClaimVerification.UNSUPPORTED
    evidence_plain = _aligned_entailment_clause(claim_plain, whole_evidence)
    if not evidence_plain:
        # Similarity located a topic but could not align a clause safely.
        return None
    # "Except the first AGM" limits which meeting may be extended. It is not
    # negation of the duration or of the Registrar's power for later meetings.
    first_excluded = bool(regex.search(first_exclusion_pattern, evidence_plain, regex.IGNORECASE))
    if first_excluded and extension_claim and first_claim and not claim_excludes_first:
        return ClaimVerification.UNSUPPORTED
    if _omits_joint_if_condition(claim_plain, evidence_plain):
        # A source requiring A and B does not establish the broader claim that
        # A alone (or B alone) is enough. Similarity can hide the omitted term.
        return ClaimVerification.UNVERIFIED
    if (
        not _is_necessary_condition(claim_plain)
        and _is_necessary_condition(evidence_plain)
        and regex.search(r"\b(?:without|always)\b", claim_plain, regex.IGNORECASE)
    ):
        return ClaimVerification.UNSUPPORTED
    claim_condition = _necessary_condition_polarity(claim_plain)
    evidence_condition = _necessary_condition_polarity(evidence_plain)
    if _is_necessary_condition(claim_plain) and _is_necessary_condition(evidence_plain):
        if claim_condition is None or evidence_condition is None:
            # The bounded grammar cannot safely compare the proposition and its
            # condition. Lexical similarity must not decide this construction.
            return ClaimVerification.UNVERIFIED
        if claim_condition != evidence_condition:
            return ClaimVerification.UNSUPPORTED
    if _is_bare_sufficient_condition(claim_plain) and _is_necessary_condition(evidence_plain):
        # "A is not valid unless B" does not establish that B alone guarantees A.
        return ClaimVerification.UNVERIFIED
    negative = regex.compile(
        rf"{_CLAUSE_NEGATION.pattern}|\bexempt\b",
        regex.IGNORECASE,
    )
    predicate_evidence = (
        regex.sub(
            r"প্রথম\s+বার্ষিক\s+সাধারণ\s+সভা(?:র\s+ক্ষেত্র)?\s+ব্যতীত|"
            r"except\s+(?:for\s+)?the\s+first\s+(?:annual\s+general\s+meeting|agm)|"
            r"(?:not|other\s+than)\s+the\s+first\s+(?:annual\s+general\s+meeting|agm)",
            " ",
            evidence_plain,
            flags=regex.IGNORECASE,
        )
        if first_excluded
        else evidence_plain
    )
    predicate_claim = (
        regex.sub(
            r"\b(?:not|except|excluding)\s+the\s+first\s+(?:annual\s+general\s+meeting|agm)",
            " ",
            claim_plain,
            flags=regex.IGNORECASE,
        )
        if first_excluded and extension_claim
        else claim_plain
    )
    scoped_predicates = _section_190_polarity_scope(predicate_claim, predicate_evidence)
    if scoped_predicates is None:
        return ClaimVerification.UNVERIFIED
    predicate_claim, predicate_evidence = scoped_predicates
    if not _suppress_negation_mismatch(claim_plain, evidence_plain) and bool(
        negative.search(predicate_claim)
    ) != bool(negative.search(predicate_evidence)):
        return ClaimVerification.UNSUPPORTED
    comparison = regex.compile(
        r"\b(?:more than|less than|at least|at most|exceed(?:s|ing)?|under|over)\b|"
        r"(?:অধিক|বেশি|কম|অন্যূন|অনধিক)",
        regex.IGNORECASE,
    )
    if comparison.search(claim_plain) and comparison.search(evidence_plain):
        claim_durations = _duration_quantities(claim_plain)
        evidence_durations = _duration_quantities(evidence_plain)
        claim_values = _digit_tokens(claim_plain) | {
            str(value) for value in _spelled_number_values(claim_plain)
        }
        evidence_values = _digit_tokens(evidence_plain) | {
            str(value) for value in _spelled_number_values(evidence_plain)
        }
        if claim_durations and evidence_durations:
            matched_claim: set[str] = set()
            matched_evidence: set[str] = set()
            for duration in claim_durations:
                match = next(
                    (
                        other
                        for other in evidence_durations
                        if _durations_equivalent(duration, other)
                    ),
                    None,
                )
                if match is None:
                    return ClaimVerification.UNSUPPORTED
                matched_claim.add(str(duration[0]))
                matched_evidence.add(str(match[0]))
            claim_values -= matched_claim
            evidence_values -= matched_evidence
        if claim_values and evidence_values and claim_values.isdisjoint(evidence_values):
            return ClaimVerification.UNSUPPORTED
    return None


def _omits_joint_if_condition(claim: str, evidence: str) -> bool:
    """Guard a bounded English construction with multiple shared prerequisites."""
    conditions = [
        match.group("condition")
        for match in regex.finditer(
            r"\bif\s+(?P<condition>[^,.;!?]+?)(?=\s+and\s+if\s+|[,.;!?]|$)",
            evidence,
            regex.IGNORECASE,
        )
    ]
    if len(conditions) < 2 or " and if " not in evidence:
        return False
    claim_terms = _significant_tokens(claim)
    if regex.search(r"\b(?:or|either)\b", claim, regex.IGNORECASE):
        return True
    return any(
        bool(required := _significant_tokens(condition)) and not required <= claim_terms
        for condition in conditions
    )


def _quantity_aligned_evidence(claim: str, evidence_texts: list[str]) -> list[str]:
    """Select clauses that bind a number to the same duty as the claim.

    Equal numbers are not interchangeable proof: an appeal period cannot prove
    a filing period, and a daily fine cannot prove a filing fee. Canonical
    bilingual subjects provide the same guard for English/Bangla evidence.
    """
    claim_subjects = _quantity_subjects(claim)
    if not claim_subjects and not _uses_lexical_verification(claim, evidence_texts):
        # Cross-language lexical overlap cannot choose a clause. Leave that choice
        # to semantic verification instead of manufacturing an empty quantity source.
        return evidence_texts
    claim_tokens = _significant_tokens(claim)
    claim_durations = _duration_quantities(claim)
    selected: list[str] = []
    zero_rate_claim = "tax" in claim_subjects and bool(
        regex.search(r"\b(?:tax.free|zero.rate|first\s+Tk)\b|\b0\s*%", claim, regex.IGNORECASE)
    )
    for evidence in evidence_texts:
        if zero_rate_claim:
            # A rate-table row may omit the word "tax" even though its heading
            # supplies that subject. Keep only the explicitly first, zero-rate
            # row; later bands cannot validate a tax-free threshold.
            selected.extend(
                line.strip()
                for line in evidence.splitlines()
                if regex.search(r"(?:প্রথম|\bfirst\b)", line, regex.IGNORECASE)
                and regex.search(r"(?:শূন্য|\bzero\b|\b0\s*%)", line, regex.IGNORECASE)
                and _numeric_money_quantities(line)
            )
        clauses = [
            piece.strip()
            for piece in regex.split(
                r"\n+|(?<=[.!?।॥。;])\s+|\s*;\s*|"
                r",\s*(?=(?:and|but|while|whereas)\b)|"
                r"\s+(?:and|but|while|whereas|এবং|কিন্তু|অথচ)\s+",
                evidence,
                flags=regex.IGNORECASE,
            )
            if piece.strip()
        ]
        if not clauses:
            continue
        evidence_has_subject_anchor = any(
            bool(claim_subjects & _quantity_subjects(clause)) for clause in clauses
        )
        if not claim_subjects:
            selected.extend(
                clause for clause in clauses if _duration_context_related(claim, clause)
            )
            continue
        ranked: list[tuple[float, int, str]] = []
        for position, clause in enumerate(clauses):
            clause_subjects = _quantity_subjects(clause)
            if claim_subjects and clause_subjects and claim_subjects.isdisjoint(clause_subjects):
                continue
            specific_claim_subjects = claim_subjects & _SPECIFIC_QUANTITY_SUBJECTS
            if specific_claim_subjects and not specific_claim_subjects & clause_subjects:
                clause_durations = _duration_quantities(clause)
                carries_claim_duration = any(
                    _durations_equivalent(left, right)
                    for left in claim_durations
                    for right in clause_durations
                )
                if clause_subjects or not (evidence_has_subject_anchor and carries_claim_duration):
                    continue
            subject_score = len(claim_subjects & clause_subjects)
            lexical_score = _coverage(claim_tokens, _significant_tokens(clause))
            if (
                not clause_subjects
                and evidence_has_subject_anchor
                and any(
                    _durations_equivalent(left, right)
                    for left in claim_durations
                    for right in _duration_quantities(clause)
                )
            ):
                subject_score = 1
            ranked.append((subject_score * 2 + lexical_score, -position, clause))
        if not ranked:
            continue
        # A compound assertion may bind a duty, deadline and sanction to
        # separate clauses.  Retain every subject-aligned clause rather than
        # allowing one best locator span to hide the others.
        selected.extend(
            clause
            for score, _position, clause in ranked
            if score >= (1.0 if claim_subjects else 0.2)
        )
        anchored_positions = {-position for score, position, _ in ranked if score >= 1.0}
        for position in sorted(anchored_positions):
            for continuation in clauses[position + 1 :]:
                if continuation.casefold() in {"or", "বা"}:
                    continue
                if not regex.match(r"^\([\p{L}\p{N}]{1,4}\)\s+", continuation):
                    break
                subjects = _quantity_subjects(continuation)
                if subjects and claim_subjects.isdisjoint(subjects):
                    break
                selected.append(continuation)
    return selected


def _quantity_scope_conflict(claim: str, evidence: str) -> bool:
    claim_subjects = _quantity_subjects(claim)
    if not claim_subjects:
        return False
    relevant_clauses = [
        clause
        for clause in regex.split(
            r"\n+|(?<=[.!?।॥。;])\s+|\s*;\s*|"
            r",\s*(?=(?:and|but|while|whereas)\b)|"
            r"\s+(?:and|but|while|whereas|এবং|কিন্তু|অথচ)\s+",
            evidence,
            flags=regex.IGNORECASE,
        )
        if _duration_quantities(clause)
    ]
    return bool(relevant_clauses) and all(
        bool(subjects := _quantity_subjects(clause)) and claim_subjects.isdisjoint(subjects)
        for clause in relevant_clauses
    )


_SPECIFIC_QUANTITY_SUBJECTS = frozenset(
    {
        "first_agm",
        "successive_agm",
        "agm_extension",
        "first_auditor",
        "auditor_term",
        "registered_office_change",
    }
)


def _has_bounded_alias(text: str, alias: str) -> bool:
    return bool(
        regex.search(
            rf"(?<![\p{{L}}\p{{M}}\p{{N}}_]){regex.escape(alias)}"
            rf"(?![\p{{L}}\p{{M}}\p{{N}}_])",
            text,
            regex.IGNORECASE,
        )
    )


def _quantity_subjects(text: str) -> set[str]:
    folded = _plain_claim_text(text).casefold()
    aliases = {
        "filing": (
            "filing",
            "file the",
            "filed",
            "return submission",
            "দাখিল",
            "জমা",
        ),
        "accounting_records": (
            "accounting books",
            "accounting records",
            "records",
            "vouchers",
            "books",
            "হিসাব-বহি",
            "হিসাব-বহিসমূহে",
            "হিসাব-বহিতে",
        ),
        "records_location": (
            "alternative location",
            "other location",
            "elsewhere",
            "অন্য যে কোন স্থানে",
            "অন্য স্থানে",
        ),
        "annual_return": (
            "annual return",
            "member list",
            "member-list",
            "annual member list",
            "বার্ষিক তালিকা",
            "তালিকা",
            "বিবরণী",
        ),
        "notice": ("notify", "notified", "notice", "নোটিশ"),
        "appeal": ("appeal", "appeal's", "আপিল", "আপিলের"),
        "fee": ("fee", "ফি"),
        "penalty": (
            "fine",
            "fined",
            "penalty",
            "sanction",
            "জরিমানা",
            "দণ্ড",
            "অর্থদণ্ড",
            "অর্থদণ্ডে",
            "দণ্ডনীয়",
            "দণ্ডনীয়",
        ),
        "daily": (
            "daily",
            "per day",
            "each day",
            "প্রতিদিন",
            "প্রতিদিনের",
            "দৈনিক",
            "প্রতি দিন",
            "প্রত্যেক দিনের",
            "প্রত্যহ",
        ),
        "retention": (
            "retention",
            "retain",
            "retained",
            "preserve",
            "preserved",
            "keep records",
            "সংরক্ষণ",
        ),
        "tax": (
            "tax",
            "income tax",
            "tax return",
            "taxpayer",
            "আয়কর",
            "আয়কর",
            "কর",
            "করদাতা",
            "কর রিটার্ন",
        ),
        "agm": ("agm", "annual general meeting", "বার্ষিক সাধারণ সভা"),
        "first_agm": (
            "first agm",
            "first annual general meeting",
            "প্রথম বার্ষিক সাধারণ সভা",
        ),
        "successive_agm": (
            "successive agm",
            "successive annual general meeting",
            "between successive",
            "দুই সভার ব্যবধান",
            "পরবর্তী বার্ষিক সাধারণ সভা",
        ),
        "agm_extension": ("agm extension", "extension by the registrar", "সময় বর্ধিত"),
        "auditor": ("auditor", "auditors", "নিরীক্ষক", "নিরীত্মগক"),
        "first_auditor": ("first auditor", "প্রথম নিরীক্ষক", "প্রথম নিরীত্মগক"),
        "auditor_term": (
            "next agm",
            "end of the next agm",
            "পরবর্তী বার্ষিক সাধারণ সভার সমাপ্তি",
        ),
        "registered_office": (
            "registered office",
            "নিবন্ধিকৃত কার্যালয়",
            "নিবন্ধিকৃত কার্যালয়",
            "নিবন্ধিকৃত কার্যালয়ে",
            "নিবন্ধিকৃত কার্যালয়ে",
            "নিবন্ধিকৃত কার্যালয়ের",
            "নিবন্ধিকৃত কার্যালয়ের",
        ),
        "registered_office_change": (
            "change in its location",
            "change of registered office",
            "কার্যালয়ের স্থান পরিবর্তন",
            "কার্যালয়ের স্থান পরিবর্তন",
        ),
    }
    return {
        subject
        for subject, values in aliases.items()
        if any(_has_bounded_alias(folded, value) for value in values)
    }


def _aligned_entailment_clause(claim: str, evidence: str) -> str:
    """Choose the clause that shares the claim's subject/predicate before polarity checks.

    For a single clause, cross-language semantic retrieval may still use the
    bounded negation and comparison guards.  For multiple cross-language clauses,
    lexical alignment is uncertain, so the guard abstains rather than applying an
    exception or prohibition from a different sentence to the claim.
    """
    sentences = [
        piece.strip()
        for piece in regex.split(r"\n+|(?<=[.!?।॥。;])\s+|\s*;\s*", evidence)
        if piece.strip()
    ]
    clauses: list[str] = []
    independent_continuation = regex.compile(
        r"\s+and\s+(?=if\s+[^,.;!?।॥。]+,\s*"
        r"(?:it|they|he|she|the\s+\w+)\s+"
        r"(?:shall|must|will|may|is|are)\b)",
        regex.IGNORECASE,
    )
    bangla_independent = regex.compile(r"\s+এবং\s+(?=যদি\s+)")
    for sentence in sentences:
        bangla_match = bangla_independent.search(sentence)
        if bangla_match is not None:
            first = sentence[: bangla_match.start()]
            # A completed permission/duty stands independently of the following
            # "and if ... then ... not" consequence. A shared consequent does not.
            if regex.search(r"(?:পারিবে|করিবে|হইবে|যাইবে)[।.!?]?\s*$", first) and not regex.search(
                r"\bযদি\b", first
            ):
                clauses.extend((first.strip(), sentence[bangla_match.end() :].strip()))
                continue
        match = independent_continuation.search(sentence)
        if match is None:
            clauses.append(sentence)
            continue
        first = sentence[: match.start()]
        # A preceding if/when/unless still governs the shared consequent.
        # Split only when the first half already makes an independent assertion.
        if regex.search(r"\b(?:if|when|unless)\b", first, regex.IGNORECASE) or not regex.search(
            r"\b(?:shall|must|will|may|is|are)\b", first, regex.IGNORECASE
        ):
            clauses.append(sentence)
            continue
        clauses.extend((first.strip(), sentence[match.start() + len(" and ") :].strip()))
    if len(clauses) <= 1:
        return clauses[0] if clauses else evidence.strip()
    claim_tokens = _significant_tokens(claim)
    ranked = [(_coverage(claim_tokens, _significant_tokens(clause)), clause) for clause in clauses]
    score, clause = max(ranked, key=lambda item: item[0])
    return clause if score >= 0.2 else ""


def _preceding_continuation_context(segments: list[str], index: int) -> str:
    """Resolve an answer continuation within its own Markdown block."""
    if index <= 0 or index >= len(segments):
        return ""
    current, preceding = segments[index], segments[index - 1]
    if not regex.match(r"^the extension\b", _plain_claim_text(current), regex.IGNORECASE):
        return ""
    if not isinstance(current, _AnswerSegment) or not isinstance(preceding, _AnswerSegment):
        return ""
    if current.block_id != preceding.block_id or current.block_kind != preceding.block_kind:
        return ""
    antecedent = _plain_claim_text(preceding).strip()
    if regex.search(r"\b(?:extend|extension)\b", antecedent, regex.IGNORECASE) and regex.search(
        r"\b(?:may|must|shall|can|is|are|does)\b", antecedent, regex.IGNORECASE
    ):
        return antecedent
    return ""


def _missing_duration(claim: str, evidence: str) -> bool:
    claim_durations = _duration_quantities(claim)
    if not claim_durations:
        return False
    evidence_durations = _duration_quantities(evidence)
    for duration in claim_durations:
        if any(_durations_equivalent(duration, other) for other in evidence_durations):
            continue
        return True
    return False


def _duration_context_related(claim: str, evidence: str) -> bool:
    if not _uses_lexical_verification(claim, [evidence]):
        return False
    ignored = {
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
        "দিন",
        "সপ্তাহ",
        "মাস",
        "বছর",
        "বৎসর",
        "বত্সর",
    }
    claim_tokens = {
        token
        for token in _significant_tokens(claim)
        if token not in ignored and not token.isdigit()
    }
    evidence_tokens = {
        token
        for token in _significant_tokens(evidence)
        if token not in ignored and not token.isdigit()
    }
    return bool(claim_tokens) and _coverage(claim_tokens, evidence_tokens) >= 0.3


def _grounding_result_from_claims(
    claims: list[AnswerClaim], *, cited_factual: int | None = None
) -> GroundingResult:
    if not claims:
        return GroundingResult(
            claims=[],
            grounded=None,
            citation_coverage=0.0,
            unverified_claim_rate=0.0,
            claims_status="no_verifiable_claims",
        )
    factual = [claim for claim in claims if claim.claim_kind != "coverage_scope"]
    invalid_scope = [
        claim
        for claim in claims
        if claim.claim_kind == "coverage_scope"
        and claim.verification is not ClaimVerification.SUPPORTED
    ]
    if not factual:
        return GroundingResult(
            claims=[claim.model_dump(mode="json") for claim in claims],
            grounded=False if invalid_scope else None,
            citation_coverage=0.0,
            unverified_claim_rate=0.0,
            claims_status=(
                "invalid_coverage_statement" if invalid_scope else "no_verifiable_claims"
            ),
        )
    supported = sum(claim.verification is ClaimVerification.SUPPORTED for claim in factual)
    unverified = sum(claim.verification is ClaimVerification.UNVERIFIED for claim in factual)
    cited = (
        cited_factual
        if cited_factual is not None
        else sum(bool(claim.evidence) for claim in factual)
    )
    total = len(factual)
    return GroundingResult(
        claims=[claim.model_dump(mode="json") for claim in claims],
        grounded=supported == total,
        citation_coverage=cited / total,
        unverified_claim_rate=unverified / total,
        claims_status="invalid_coverage_statement" if invalid_scope else None,
    )


def _reviewed_claim_spans(
    chunk: ContextChunk,
    fallback: _SelectedSpan | None,
    *,
    requirement_ids: set[str] | None = None,
) -> list[_SelectedSpan | None]:
    """Retain all reviewed heading, row and exception spans, with exact offsets."""
    spans: list[_SelectedSpan | None] = []
    seen: set[str] = set()
    for item in chunk.metadata.get("reviewed_proof", []):
        if requirement_ids is not None and item.get("requirement_id") not in requirement_ids:
            continue
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote or quote in seen:
            continue
        start = chunk.content.find(quote)
        if start < 0:
            continue
        seen.add(quote)
        spans.append(_SelectedSpan(quote, start, start + len(quote), "reviewed_proof", None, False))
    return spans or [fallback]


def _evidence_snapshot(
    citation_index: int,
    chunk: ContextChunk,
    config: ChatConfig,
    span: _SelectedSpan | None = None,
) -> ClaimEvidence:
    source = span.text if span is not None else chunk.content
    excerpt = (
        proof_preview(source, config.citation_excerpt_max_chars)
        if config.citation_excerpt_max_chars > 0
        else None
    )
    is_web = chunk.metadata.get("source_kind") == CitationSourceKind.WEB.value
    span_hash = (
        None
        if is_web
        else (
            content_hash(span.text)
            if span is not None
            else chunk.metadata.get("evidence_span_hash")
        )
    )
    page_number, source_start, source_end = _proof_locator(chunk, span)
    return ClaimEvidence(
        citation_index=citation_index,
        chunk_id=None if is_web else chunk.chunk_id,
        document_id=None if is_web else chunk.document_id,
        filename=chunk.filename,
        chunk_index=None if is_web else chunk.chunk_index,
        page_number=None if is_web else page_number,
        char_start=None if is_web else source_start,
        char_end=None if is_web else source_end,
        excerpt=excerpt,
        evidence_unit_id=None if is_web else chunk.metadata.get("evidence_unit_id"),
        evidence_span_hash=span_hash,
        source_kind=CitationSourceKind.WEB if is_web else CitationSourceKind.KNOWLEDGE,
        web_url=chunk.metadata.get("web_url") if is_web else None,
        web_title=chunk.metadata.get("web_title") if is_web else None,
        web_retrieved_at=chunk.metadata.get("web_retrieved_at") if is_web else None,
        web_provider=chunk.metadata.get("web_provider") if is_web else None,
    )


def _proof_locator(
    chunk: ContextChunk, span: _SelectedSpan | None
) -> tuple[int | None, int | None, int | None]:
    """Locate the quoted proof, never a repeated/reconstructed chunk heading."""
    text = span.text if span is not None else chunk.content
    matches: set[tuple[int | None, int, int]] = set()
    for item in chunk.metadata.get("source_spans", []):
        if not isinstance(item, dict) or item.get("provenance") != "exact_source_span":
            continue
        source = item.get("text")
        start, end = item.get("char_start"), item.get("char_end")
        if (
            not isinstance(source, str)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or end - start != len(source)
        ):
            continue
        position = source.find(text)
        if position < 0 or source.find(text, position + 1) >= 0:
            continue
        page_start, page_end = item.get("page_start"), item.get("page_end")
        page = page_start if isinstance(page_start, int) and page_start == page_end else None
        matches.add((page, start + position, start + position + len(text)))
    if len(matches) == 1:
        return next(iter(matches))
    reconstructed = (
        chunk.metadata.get("evidence_source_envelope") == "reconstructed_context"
        or chunk.metadata.get("provenance_precision") == "chunk_with_source_spans"
        or chunk.metadata.get("heading_context_status") == "preserved"
        or bool(chunk.metadata.get("table_context") or chunk.metadata.get("table_row_group"))
    )
    if reconstructed or len(matches) > 1:
        return None, None, None
    return (
        chunk.page_number,
        chunk.char_start + (span.char_start if span is not None else 0)
        if chunk.char_start is not None
        else None,
        chunk.char_start + span.char_end
        if span is not None and chunk.char_start is not None
        else chunk.char_end,
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
    # Indic digit glyphs alone do not make otherwise English passages
    # cross-language evidence; token and amount checks already fold them.
    claim_language = detect_language(_fold_indic_digits(claim))
    evidence_language = detect_language(_fold_indic_digits(evidence))
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
