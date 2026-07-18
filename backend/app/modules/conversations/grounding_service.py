"""Deterministic evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

from dataclasses import dataclass

import regex

from app.core.config import ChatConfig
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import (
    AnswerClaim,
    ClaimEvidence,
    InsufficientEvidenceReason,
)
from app.platform.domain.text_tokenization import tokenize

_SEGMENT_PATTERN = regex.compile(
    r"(?<=[.!?।॥。\uff01\uff1f…])\s+|\n+",
    regex.UNICODE,
)
_CITATION_PATTERN = regex.compile(r"\[(\d+)\]")
_LEADING_CITATIONS_PATTERN = regex.compile(r"^((?:\[\d+\]\s*)+)(.*)$", regex.DOTALL)
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


@dataclass(frozen=True, slots=True)
class GroundingResult:
    claims: list[dict]
    grounded: bool
    citation_coverage: float


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
        best_score = max(chunk.score for chunk in chunks)
        if best_score < self._config.minimum_evidence_score:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
                best_score=best_score,
            )
        query_tokens = _significant_tokens(question)
        evidence_tokens: set[str] = set()
        for chunk in chunks:
            evidence_tokens.update(_significant_tokens(chunk.content))
        coverage = _coverage(query_tokens, evidence_tokens)
        if coverage < self._config.minimum_query_token_coverage and best_score < 0.65:
            return EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.LOW_QUERY_EVIDENCE_COVERAGE,
                query_token_coverage=coverage,
                best_score=best_score,
            )
        return EvidenceDecision(
            sufficient=True,
            query_token_coverage=coverage,
            best_score=best_score,
        )

    def map_claims(self, answer: str, chunks: list[ContextChunk]) -> GroundingResult:
        claims: list[AnswerClaim] = []
        supported = 0
        cited = 0
        for index, raw_segment in enumerate(_answer_segments(answer), start=1):
            segment = raw_segment.strip()
            if not segment:
                continue
            citation_indexes = [int(value) for value in _CITATION_PATTERN.findall(segment)]
            claim_text = _CITATION_PATTERN.sub("", segment).strip()
            if not claim_text:
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
            grounded = bool(evidence_chunks) and (
                _coverage(claim_tokens, evidence_tokens)
                >= self._config.minimum_claim_token_coverage
            )
            supported += int(grounded)
            cited += int(has_valid_citation)
            claims.append(
                AnswerClaim(
                    claim_id=f"claim-{index}",
                    text=claim_text,
                    grounded=grounded,
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
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)
