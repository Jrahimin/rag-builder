"""Deterministic evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import regex

from app.core.config import ChatConfig, EvidenceScoreMode
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import (
    AnswerClaim,
    ClaimEvidence,
    ClaimVerification,
    InsufficientEvidenceReason,
)
from app.platform.domain.text_tokenization import tokenize

_SEGMENT_PATTERN = regex.compile(
    r"(?<=[.!?।॥。\uff01\uff1f…])\s+|\n+",
    regex.UNICODE,
)
_CITATION_PATTERN = regex.compile(r"\[(\d+)\]")
_LEADING_CITATIONS_PATTERN = regex.compile(r"^((?:\[\d+\]\s*)+)(.*)$", regex.DOTALL)
_MARKDOWN_HEADING_PATTERN = regex.compile(r"^#{1,6}\s+\S.*$")
_MARKDOWN_ORDINAL_PATTERN = regex.compile(r"^(?:[-*+]\s*)?\p{Number}+[.)]?$")
_MARKDOWN_TABLE_DIVIDER_PATTERN = regex.compile(r"^\|?[\s:|-]+\|?$")
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
    evidence_char_start: int | None = None
    evidence_char_end: int | None = None


@dataclass(frozen=True, slots=True)
class GroundingResult:
    claims: list[dict]
    grounded: bool
    citation_coverage: float
    unverified_claim_rate: float = 0.0


class GroundingService:
    """Apply measured thresholds without asking the generator to self-grade."""

    def __init__(self, config: ChatConfig) -> None:
        self._config = config

    def assess(self, question: str, chunks: list[ContextChunk]) -> EvidenceDecision:
        if not chunks:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS,
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
            )
        best = max(scored, key=lambda item: (item[0], item[1], str(item[2].chunk_id)))
        direct = [
            item
            for item in scored
            if item[0] >= self._config.minimum_semantic_evidence_score
        ]
        if direct:
            winner = max(direct, key=lambda item: (item[0], item[1], str(item[2].chunk_id)))
            return EvidenceDecision(
                sufficient=True,
                query_token_coverage=winner[1],
                best_score=winner[0],
                winning_chunk_id=winner[2].chunk_id,
                evidence_score_method=self._evidence_score_method,
                evidence_char_start=winner[3],
                evidence_char_end=winner[4],
            )
        rescued = [
            item
            for item in scored
            if item[0] >= self._config.lexical_corroboration_floor_score
            and item[1] >= self._config.lexical_corroboration_coverage
        ]
        if rescued:
            winner = max(rescued, key=lambda item: (item[0], item[1], str(item[2].chunk_id)))
            return EvidenceDecision(
                sufficient=True,
                query_token_coverage=winner[1],
                best_score=winner[0],
                lexically_corroborated=True,
                winning_chunk_id=winner[2].chunk_id,
                evidence_score_method=self._evidence_score_method,
                evidence_char_start=winner[3],
                evidence_char_end=winner[4],
            )
        return EvidenceDecision(
            sufficient=False,
            reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
            query_token_coverage=best[1],
            best_score=best[0],
            winning_chunk_id=best[2].chunk_id,
            evidence_score_method=self._evidence_score_method,
            evidence_char_start=best[3],
            evidence_char_end=best[4],
        )

    @property
    def _evidence_score_method(self) -> str:
        if self._config.evidence_score_mode is EvidenceScoreMode.PASSAGE_MAX:
            return "bounded_token_max_v1"
        return "whole_chunk_cosine"

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

    def map_claims(self, answer: str, chunks: list[ContextChunk]) -> GroundingResult:
        claims: list[AnswerClaim] = []
        supported = 0
        unverified = 0
        cited = 0
        for index, raw_segment in enumerate(_answer_segments(answer), start=1):
            segment = raw_segment.strip()
            if not segment:
                continue
            citation_indexes = [int(value) for value in _CITATION_PATTERN.findall(segment)]
            claim_text = _CITATION_PATTERN.sub("", segment).strip()
            if not claim_text or _is_structural_segment(claim_text):
                continue
            evidence_chunks = [
                (citation_index, chunks[citation_index - 1])
                for citation_index in dict.fromkeys(citation_indexes)
                if 1 <= citation_index <= len(chunks)
            ]
            has_valid_citation = bool(evidence_chunks)
            if not evidence_chunks:
                best = _best_evidence(claim_text, chunks)
                if best is not None:
                    evidence_chunks = [best]
            claim_tokens = _significant_tokens(claim_text)
            evidence_tokens: set[str] = set()
            for _, chunk in evidence_chunks:
                evidence_tokens.update(_significant_tokens(chunk.content))
            shared_tokens = claim_tokens & evidence_tokens
            coverage = _coverage(claim_tokens, evidence_tokens)
            if evidence_chunks and coverage >= self._config.minimum_claim_token_coverage:
                verification = ClaimVerification.SUPPORTED
            elif has_valid_citation and not shared_tokens:
                verification = ClaimVerification.UNVERIFIED
            else:
                verification = ClaimVerification.UNSUPPORTED
            grounded = verification is not ClaimVerification.UNSUPPORTED
            supported += int(grounded)
            unverified += int(verification is ClaimVerification.UNVERIFIED)
            cited += int(has_valid_citation)
            claims.append(
                AnswerClaim(
                    claim_id=f"claim-{index}",
                    text=claim_text,
                    grounded=grounded,
                    verification=verification,
                    evidence=[
                        _evidence_snapshot(citation_index, chunk, self._config)
                        for citation_index, chunk in evidence_chunks
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


def _best_evidence(text: str, chunks: list[ContextChunk]) -> tuple[int, ContextChunk] | None:
    claim_tokens = _significant_tokens(text)
    ranked = [
        (_coverage(claim_tokens, _significant_tokens(chunk.content)), index, chunk)
        for index, chunk in enumerate(chunks, start=1)
    ]
    if not ranked:
        return None
    score, index, chunk = max(ranked, key=lambda item: (item[0], item[2].score, -item[1]))
    return (index, chunk) if score > 0.0 else None


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
    """Exclude Markdown scaffolding that does not assert a factual claim."""
    stripped = text.strip()
    return bool(
        _MARKDOWN_HEADING_PATTERN.fullmatch(stripped)
        or _MARKDOWN_ORDINAL_PATTERN.fullmatch(stripped)
        or _MARKDOWN_TABLE_DIVIDER_PATTERN.fullmatch(stripped)
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


def _coverage(expected: set[str], actual: set[str]) -> float:
    """Raw query-token coverage. Corpus-IDF weighting was compared and not selected."""
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)
