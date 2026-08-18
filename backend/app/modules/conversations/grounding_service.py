"""Deterministic evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import regex

from app.core.config import ChatConfig, EvidenceGateMode, EvidenceScoreMode, QueryTranslationConfig
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import (
    AnswerClaim,
    ClaimEvidence,
    ClaimVerification,
    InsufficientEvidenceReason,
)
from app.platform.domain.language_detection import detect_language
from app.platform.domain.text_tokenization import tokenize
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.query_translation import (
    BaseQueryTranslationProvider,
    QueryTranslationRequest,
)
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
_LIST_PREAMBLE_PATTERN = regex.compile(r"^[^.\n!?।॥。\uff01\uff1f…]+[:：—–]\s*$")
_POLARITY_PATTERN = regex.compile(r"^(?:yes|no|না|হ্যাঁ)[.\u0964]?\s*$", regex.IGNORECASE)
_INSUFFICIENCY_MARKER = "not enough indexed evidence"
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
    best_score: float = 0.0
    lexically_corroborated: bool = False
    winning_chunk_id: uuid.UUID | None = None
    evidence_score_method: str = "whole_chunk_cosine"
    evidence_calibration_id: str = "whole_chunk_cosine:v1"
    evidence_char_start: int | None = None
    evidence_char_end: int | None = None
    winning_semantic_score: float | None = None
    winning_rank_score: float | None = None


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


class GroundingService:
    """Apply measured thresholds without asking the generator to self-grade."""

    def __init__(
        self,
        config: ChatConfig,
        embedder: BaseEmbeddingProvider | None = None,
        translator: BaseQueryTranslationProvider | None = None,
        translation_config: QueryTranslationConfig | None = None,
    ) -> None:
        self._config = config
        self._embedder = embedder
        self._translator = translator
        self._translation_config = translation_config

    def assess(self, question: str, chunks: list[ContextChunk]) -> EvidenceDecision:
        if not chunks:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS,
            )
        if _rerank_applied(chunks):
            return self._assess_reranker(chunks)
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
            item
            for item in scored
            if item[0] >= self._config.minimum_semantic_evidence_score
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

    def _assess_reranker(self, chunks: list[ContextChunk]) -> EvidenceDecision:
        scored = [
            (chunk.rerank_relevance_score, chunk)
            for chunk in chunks
            if chunk.rerank_relevance_score is not None
        ]
        method = "reranker_relevance"
        calibration_id = "reranker_relevance:v1"
        if not scored:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
                evidence_score_method=method,
                evidence_calibration_id=calibration_id,
            )
        winner = max(scored, key=lambda item: (item[0], str(item[1].chunk_id)))
        if winner[0] >= self._config.minimum_reranker_evidence_score:
            return EvidenceDecision(
                sufficient=True,
                best_score=winner[0],
                winning_chunk_id=winner[1].chunk_id,
                evidence_score_method=method,
                evidence_calibration_id=calibration_id,
                evidence_char_start=winner[1].char_start,
                evidence_char_end=winner[1].char_end,
                winning_semantic_score=winner[1].semantic_score,
                winning_rank_score=winner[1].rank_score,
            )
        return EvidenceDecision(
            sufficient=False,
            reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
            best_score=winner[0],
            winning_chunk_id=winner[1].chunk_id,
            evidence_score_method=method,
            evidence_calibration_id=calibration_id,
            evidence_char_start=winner[1].char_start,
            evidence_char_end=winner[1].char_end,
            winning_semantic_score=winner[1].semantic_score,
            winning_rank_score=winner[1].rank_score,
        )

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
            "winning_char_start": decision.evidence_char_start,
            "winning_char_end": decision.evidence_char_end,
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

    async def map_claims(self, answer: str, chunks: list[ContextChunk]) -> GroundingResult:
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
            drafts.append(
                _ClaimDraft(
                    index=index,
                    text=claim_text,
                    evidence_chunks=evidence_chunks,
                    has_valid_citation=bool(evidence_chunks),
                )
            )
            if evidence_chunks and not _uses_lexical_verification(
                claim_text,
                [chunk.content for _, chunk in evidence_chunks],
            ):
                semantic_pairs.extend((claim_text, chunk.content) for _, chunk in evidence_chunks)
        similarities = await self._claim_similarities(semantic_pairs)
        translations = await self._claim_translations(drafts)

        claims: list[AnswerClaim] = []
        supported = 0
        unverified = 0
        cited = 0
        for draft in drafts:
            evidence_texts = [chunk.content for _, chunk in draft.evidence_chunks]
            if not draft.has_valid_citation:
                verification = ClaimVerification.UNSUPPORTED
            elif _uses_lexical_verification(draft.text, evidence_texts):
                verification = self._lexical_verification(draft.text, evidence_texts)
            else:
                translated = translations.get(draft.text)
                if translated:
                    verification = self._lexical_verification(translated, evidence_texts)
                else:
                    scores = [
                        similarities.get((draft.text, chunk.content))
                        for _, chunk in draft.evidence_chunks
                    ]
                    numeric = [value for value in scores if value is not None]
                    score = max(numeric) if numeric else None
                    verification = self._cross_language_verification(score)
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
        texts = list(dict.fromkeys(text for pair in unique_pairs for text in pair))
        try:
            embedded = await embedder.embed_texts(texts)
        except ProviderError:
            return missing
        by_text = dict(zip(texts, embedded.vectors, strict=True))
        return {
            pair: cosine_similarity(by_text[pair[0]], by_text[pair[1]]) for pair in unique_pairs
        }

    async def _claim_translations(self, drafts: list[_ClaimDraft]) -> dict[str, str]:
        if not self._translator_enabled():
            return {}
        unique: dict[str, list[str]] = {}
        for draft in drafts:
            if not draft.has_valid_citation:
                continue
            evidence_texts = [chunk.content for _, chunk in draft.evidence_chunks]
            if _uses_lexical_verification(draft.text, evidence_texts):
                continue
            unique.setdefault(draft.text, evidence_texts)
        translated: dict[str, str] = {}
        for claim_text, evidence_texts in unique.items():
            result = await self._translate_claim(claim_text, evidence_texts)
            if result:
                translated[claim_text] = result
        return translated

    def _translator_enabled(self) -> bool:
        if self._translator is None:
            return False
        if self._translation_config is None:
            return True
        return self._translation_config.enabled

    async def _translate_claim(self, claim: str, evidence_texts: list[str]) -> str | None:
        translator = self._translator
        if translator is None:
            return None
        target = _evidence_target_language(evidence_texts)
        if target is None:
            return None
        source = detect_language(claim)
        config = self._translation_config or QueryTranslationConfig(enabled=True)
        try:
            response = await translator.translate(
                QueryTranslationRequest(
                    query=claim,
                    source_profile=source.primary_language or "en",
                    target_language=target,
                    prompt_version=config.prompt_version,
                    max_output_tokens=config.max_output_tokens,
                )
            )
        except ProviderError:
            return None
        translated = response.translated_query.strip()
        return translated or None


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
    return _INSUFFICIENCY_MARKER in text.casefold()


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
    return ClaimEvidence(
        citation_index=citation_index,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        filename=chunk.filename,
        chunk_index=chunk.chunk_index,
        page_number=chunk.page_number,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        excerpt=excerpt,
    )


def _significant_tokens(text: str) -> set[str]:
    return {token for token in tokenize(text, for_query=True) if token not in _ENGLISH_STOPWORDS}


def _evidence_target_language(evidence_texts: list[str]) -> str | None:
    languages = [
        detect_language(text).primary_language
        for text in evidence_texts
        if text.strip()
    ]
    known = [language for language in languages if language]
    if not known:
        return None
    return max(set(known), key=known.count)


def _uses_lexical_verification(claim: str, evidence_texts: list[str]) -> bool:
    """Same-script claims keep the lexical validator; mixed scripts do not."""
    if not evidence_texts:
        return False
    return all(_same_language(claim, text) for text in evidence_texts)


def _same_language(claim: str, evidence: str) -> bool:
    claim_language = detect_language(claim)
    evidence_language = detect_language(evidence)
    if claim_language.primary_language is None or evidence_language.primary_language is None:
        return True
    if claim_language.is_mixed or evidence_language.is_mixed:
        return False
    return claim_language.primary_language == evidence_language.primary_language


def _usable_embedder(embedder: BaseEmbeddingProvider | None) -> bool:
    return embedder is not None and embedder.provider_name != "hash"


def _coverage(expected: set[str], actual: set[str]) -> float:
    """Raw query-token coverage. Corpus-IDF weighting was compared and not selected."""
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)


def _rerank_applied(chunks: list[ContextChunk]) -> bool:
    return any(
        chunk.rerank_relevance_score is not None
        and str(chunk.metadata.get("rerank_status")) == "applied"
        for chunk in chunks
    )
