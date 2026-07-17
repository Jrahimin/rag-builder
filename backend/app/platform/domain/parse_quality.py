"""Unicode-first parse quality assessment for document extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import regex

from app.platform.domain.language_detection import detect_language
from app.platform.domain.text_tokenization import tokenize

_CONTROL_PATTERN = regex.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", regex.UNICODE)
_REPLACEMENT_CHAR = "\ufffd"
_PRIVATE_USE_PATTERN = regex.compile(r"[\uE000-\uF8FF]", regex.UNICODE)
_SURROGATE_PATTERN = regex.compile(r"[\uD800-\uDFFF]", regex.UNICODE)
_PRINTABLE_PATTERN = regex.compile(r"[\P{C}]", regex.UNICODE)
_LETTER_PATTERN = regex.compile(r"\p{Letter}", regex.UNICODE)
_SUSPICIOUS_TOKEN_CHAR_PATTERN = regex.compile(r"[\{\}\\|\$\^@_?]", regex.UNICODE)
_RAW_WORD_PATTERN = regex.compile(r"\S+", regex.UNICODE)
_MIN_SEGMENT_WORDS = 2
_SEGMENT_SHORT_BLOCK_CHARS = 120
_UNNATURAL_WORD_RATIO_THRESHOLD = 0.05
_UNNATURAL_WORD_RATIO_PENALTY = 2.0
_SEGMENT_MIN_SCORE_BUFFER = 0.08


class ExtractionMethod(StrEnum):
    """How the accepted document text was primarily obtained."""

    NATIVE_TEXT = "native_text"
    FALLBACK_PARSER = "fallback_parser"
    OCR = "ocr"
    MIXED = "mixed"


class PageExtractionStatus(StrEnum):
    """Final status for a single page after candidate selection."""

    ACCEPTED = "accepted"
    FALLBACK_PARSER_USED = "fallback_parser_used"
    OCR_USED = "ocr_used"
    UNRECOVERABLE = "unrecoverable"
    EMPTY_OR_IMAGE_ONLY = "empty_or_image_only"


class CandidateSelectionStatus(StrEnum):
    """Whether a parser/OCR attempt was selected or rejected."""

    SELECTED = "selected"
    REJECTED = "rejected"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class ParseQualitySignals:
    """Raw quality signal ratios used to compute the normalized score."""

    printable_ratio: float
    control_ratio: float
    replacement_ratio: float
    private_use_ratio: float
    surrogate_ratio: float
    letter_ratio: float
    token_count: int
    char_count: int
    script_coherence: float
    unnatural_word_ratio: float
    segment_min_score: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "printable_ratio": self.printable_ratio,
            "control_ratio": self.control_ratio,
            "replacement_ratio": self.replacement_ratio,
            "private_use_ratio": self.private_use_ratio,
            "surrogate_ratio": self.surrogate_ratio,
            "letter_ratio": self.letter_ratio,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "script_coherence": self.script_coherence,
            "unnatural_word_ratio": self.unnatural_word_ratio,
            "segment_min_score": self.segment_min_score,
        }


@dataclass(frozen=True, slots=True)
class ParseQualityAssessment:
    """Normalized quality assessment for extracted text."""

    score: float
    signals: ParseQualitySignals
    is_empty: bool
    is_acceptable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "is_empty": self.is_empty,
            "is_acceptable": self.is_acceptable,
            "signals": self.signals.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ParserAttemptRecord:
    """One parser or OCR attempt for a page."""

    parser_id: str
    parser_version: str | None
    duration_ms: int
    parse_quality_score: float
    selection_status: CandidateSelectionStatus
    rejection_reason: str | None = None
    ocr_provider: str | None = None
    ocr_model: str | None = None
    ocr_version: str | None = None
    ocr_confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "parser_id": self.parser_id,
            "parser_version": self.parser_version,
            "duration_ms": self.duration_ms,
            "parse_quality_score": self.parse_quality_score,
            "selection_status": self.selection_status.value,
            "rejection_reason": self.rejection_reason,
        }
        if self.ocr_provider is not None:
            payload["ocr_provider"] = self.ocr_provider
        if self.ocr_model is not None:
            payload["ocr_model"] = self.ocr_model
        if self.ocr_version is not None:
            payload["ocr_version"] = self.ocr_version
        if self.ocr_confidence is not None:
            payload["ocr_confidence"] = self.ocr_confidence
        return payload


@dataclass(frozen=True, slots=True)
class PageExtractionRecord:
    """Final extraction outcome for one page."""

    page_number: int
    text: str
    status: PageExtractionStatus
    parse_quality_score: float
    accepted_parser: str
    extraction_method: ExtractionMethod
    attempts: tuple[ParserAttemptRecord, ...] = field(default_factory=tuple)
    char_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page_number,
            "status": self.status.value,
            "parse_quality_score": self.parse_quality_score,
            "accepted_parser": self.accepted_parser,
            "extraction_method": self.extraction_method.value,
            "char_count": self.char_count,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


@dataclass(frozen=True, slots=True)
class ParseQualitySummary:
    """Document-level rollup derived from page extractions."""

    parse_quality_score: float
    parser_confidence: float
    extraction_method: ExtractionMethod
    accepted_parser: str
    accepted_page_count: int
    failed_page_count: int
    empty_page_count: int
    total_page_count: int
    success_ratio: float
    ocr_used: bool
    ocr_page_count: int
    fallback_page_count: int
    partial_extraction: bool
    min_page_quality: float | None
    ocr_quality: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "parse_quality_score": self.parse_quality_score,
            "parser_confidence": self.parser_confidence,
            "extraction_method": self.extraction_method.value,
            "accepted_parser": self.accepted_parser,
            "accepted_page_count": self.accepted_page_count,
            "failed_page_count": self.failed_page_count,
            "empty_page_count": self.empty_page_count,
            "total_page_count": self.total_page_count,
            "success_ratio": self.success_ratio,
            "ocr_used": self.ocr_used,
            "ocr_page_count": self.ocr_page_count,
            "fallback_page_count": self.fallback_page_count,
            "partial_extraction": self.partial_extraction,
            "min_page_quality": self.min_page_quality,
            "ocr_quality": self.ocr_quality,
        }


class ParseQualityScorer:
    """Score extracted text using Unicode-property heuristics."""

    def __init__(
        self,
        *,
        min_page_quality_score: float = 0.55,
        min_text_chars: int = 20,
    ) -> None:
        self._min_page_quality_score = min_page_quality_score
        self._min_text_chars = min_text_chars

    def assess(self, text: str) -> ParseQualityAssessment:
        stripped = text.strip()
        if not stripped:
            signals = _empty_signals()
            return ParseQualityAssessment(
                score=0.0,
                signals=signals,
                is_empty=True,
                is_acceptable=False,
            )

        char_count = len(stripped)
        control_count = len(_CONTROL_PATTERN.findall(stripped))
        replacement_count = stripped.count(_REPLACEMENT_CHAR)
        private_use_count = len(_PRIVATE_USE_PATTERN.findall(stripped))
        surrogate_count = len(_SURROGATE_PATTERN.findall(stripped))
        printable_count = len(_PRINTABLE_PATTERN.findall(stripped))
        letter_count = len(_LETTER_PATTERN.findall(stripped))
        tokens = tokenize(stripped)

        printable_ratio = printable_count / char_count
        control_ratio = control_count / char_count
        replacement_ratio = replacement_count / char_count
        private_use_ratio = private_use_count / char_count
        surrogate_ratio = surrogate_count / char_count
        letter_ratio = letter_count / char_count if char_count else 0.0
        script_coherence = _script_coherence(stripped)
        unnatural_word_ratio = _unnatural_word_ratio(stripped)
        segment_min_score = _segment_min_quality(stripped)

        penalty = (
            control_ratio * 4.0
            + replacement_ratio * 5.0
            + private_use_ratio * 4.0
            + surrogate_ratio * 5.0
            + max(0.0, 0.15 - letter_ratio) * 2.0
            + max(0.0, 0.5 - script_coherence) * 1.5
            + max(0.0, unnatural_word_ratio - _UNNATURAL_WORD_RATIO_THRESHOLD)
            * _UNNATURAL_WORD_RATIO_PENALTY
        )
        score = max(0.0, min(1.0, printable_ratio - penalty))
        score = min(score, segment_min_score + _SEGMENT_MIN_SCORE_BUFFER)
        if len(tokens) <= 1 and char_count > self._min_text_chars:
            score = min(score, 0.35)

        signals = ParseQualitySignals(
            printable_ratio=round(printable_ratio, 4),
            control_ratio=round(control_ratio, 4),
            replacement_ratio=round(replacement_ratio, 4),
            private_use_ratio=round(private_use_ratio, 4),
            surrogate_ratio=round(surrogate_ratio, 4),
            letter_ratio=round(letter_ratio, 4),
            token_count=len(tokens),
            char_count=char_count,
            script_coherence=round(script_coherence, 4),
            unnatural_word_ratio=round(unnatural_word_ratio, 4),
            segment_min_score=round(segment_min_score, 4),
        )
        is_acceptable = (
            score >= self._min_page_quality_score
            and char_count >= self._min_text_chars
            and letter_ratio >= 0.05
        )
        return ParseQualityAssessment(
            score=round(score, 4),
            signals=signals,
            is_empty=False,
            is_acceptable=is_acceptable,
        )


def _empty_signals() -> ParseQualitySignals:
    return ParseQualitySignals(
        printable_ratio=0.0,
        control_ratio=0.0,
        replacement_ratio=0.0,
        private_use_ratio=0.0,
        surrogate_ratio=0.0,
        letter_ratio=0.0,
        token_count=0,
        char_count=0,
        script_coherence=0.0,
        unnatural_word_ratio=0.0,
        segment_min_score=0.0,
    )


def _unnatural_word_ratio(text: str) -> float:
    words = _RAW_WORD_PATTERN.findall(text)
    if not words:
        return 0.0
    unnatural_count = sum(1 for word in words if _SUSPICIOUS_TOKEN_CHAR_PATTERN.search(word))
    return unnatural_count / len(words)


def _split_quality_segments(text: str) -> list[str]:
    segments: list[str] = []
    for block in regex.split(r"\n+", text.strip()):
        block = block.strip()
        if not block:
            continue
        if len(block) <= _SEGMENT_SHORT_BLOCK_CHARS:
            segments.append(block)
            continue
        segments.extend(
            segment.strip() for segment in regex.split(r"(?<=[.!?])\s+", block) if segment.strip()
        )
    return segments


def _segment_lexical_score(text: str) -> float:
    words = _RAW_WORD_PATTERN.findall(text)
    if len(words) < _MIN_SEGMENT_WORDS:
        return 1.0
    unnatural_word_ratio = _unnatural_word_ratio(text)
    score = 1.0
    if unnatural_word_ratio > _UNNATURAL_WORD_RATIO_THRESHOLD:
        score -= unnatural_word_ratio * _UNNATURAL_WORD_RATIO_PENALTY
    return max(0.0, score)


def _segment_min_quality(text: str) -> float:
    segments = _split_quality_segments(text)
    if not segments:
        return 1.0
    return min(_segment_lexical_score(segment) for segment in segments)


def _script_coherence(text: str) -> float:
    language = detect_language(text)
    if not language.languages:
        return 0.0
    if language.is_mixed:
        return max(language.confidence, 0.4)
    return language.confidence


def summarize_page_extractions(
    pages: tuple[PageExtractionRecord, ...],
    *,
    total_page_count: int,
) -> ParseQualitySummary:
    """Derive document-level quality metrics from page outcomes."""
    if total_page_count <= 0:
        return ParseQualitySummary(
            parse_quality_score=0.0,
            parser_confidence=0.0,
            extraction_method=ExtractionMethod.NATIVE_TEXT,
            accepted_parser="none",
            accepted_page_count=0,
            failed_page_count=0,
            empty_page_count=0,
            total_page_count=0,
            success_ratio=0.0,
            ocr_used=False,
            ocr_page_count=0,
            fallback_page_count=0,
            partial_extraction=False,
            min_page_quality=None,
            ocr_quality=None,
        )

    accepted_pages = [
        page
        for page in pages
        if page.status
        in {
            PageExtractionStatus.ACCEPTED,
            PageExtractionStatus.FALLBACK_PARSER_USED,
            PageExtractionStatus.OCR_USED,
        }
        and page.text.strip()
    ]
    failed_pages = [page for page in pages if page.status is PageExtractionStatus.UNRECOVERABLE]
    empty_pages = [
        page
        for page in pages
        if page.status is PageExtractionStatus.EMPTY_OR_IMAGE_ONLY or not page.text.strip()
    ]

    total_chars = sum(page.char_count for page in accepted_pages)
    if total_chars > 0:
        weighted_quality = (
            sum(page.parse_quality_score * page.char_count for page in accepted_pages) / total_chars
        )
    else:
        weighted_quality = 0.0

    method_counts: dict[ExtractionMethod, int] = {}
    parser_counts: dict[str, int] = {}
    ocr_confidences: list[float] = []
    accepted_qualities: list[float] = []

    for page in accepted_pages:
        method_counts[page.extraction_method] = method_counts.get(page.extraction_method, 0) + 1
        parser_counts[page.accepted_parser] = parser_counts.get(page.accepted_parser, 0) + 1
        accepted_qualities.append(page.parse_quality_score)
        for attempt in page.attempts:
            if (
                attempt.selection_status is CandidateSelectionStatus.SELECTED
                and attempt.ocr_confidence is not None
            ):
                ocr_confidences.append(attempt.ocr_confidence)

    extraction_method = _dominant_extraction_method(method_counts)
    accepted_parser = max(parser_counts, key=parser_counts.get) if parser_counts else "none"  # type: ignore[arg-type]

    accepted_count = len(accepted_pages)
    success_ratio = accepted_count / total_page_count
    ocr_page_count = sum(1 for page in pages if page.status is PageExtractionStatus.OCR_USED)
    fallback_page_count = sum(
        1 for page in pages if page.status is PageExtractionStatus.FALLBACK_PARSER_USED
    )
    parser_confidence = max(
        0.1,
        min(1.0, success_ratio - (len(failed_pages) * 0.15 / total_page_count)),
    )

    return ParseQualitySummary(
        parse_quality_score=round(weighted_quality, 4),
        parser_confidence=round(parser_confidence, 4),
        extraction_method=extraction_method,
        accepted_parser=accepted_parser,
        accepted_page_count=accepted_count,
        failed_page_count=len(failed_pages),
        empty_page_count=len(empty_pages),
        total_page_count=total_page_count,
        success_ratio=round(success_ratio, 4),
        ocr_used=ocr_page_count > 0,
        ocr_page_count=ocr_page_count,
        fallback_page_count=fallback_page_count,
        partial_extraction=bool(failed_pages or empty_pages) and accepted_count > 0,
        min_page_quality=min(accepted_qualities) if accepted_qualities else None,
        ocr_quality=round(sum(ocr_confidences) / len(ocr_confidences), 4)
        if ocr_confidences
        else None,
    )


def _dominant_extraction_method(method_counts: dict[ExtractionMethod, int]) -> ExtractionMethod:
    if not method_counts:
        return ExtractionMethod.NATIVE_TEXT
    if len(method_counts) > 1:
        return ExtractionMethod.MIXED
    return next(iter(method_counts))
