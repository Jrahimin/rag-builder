"""Page-level PDF extraction workflow with quality-gated parser fallback."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import fitz
import structlog

from app.core.config import OcrConfig, ParsingConfig, get_settings
from app.platform.domain.language_detection import detect_language
from app.platform.domain.parse_quality import (
    CandidateSelectionStatus,
    ExtractionMethod,
    PageExtractionRecord,
    PageExtractionStatus,
    ParseQualityScorer,
    ParseQualitySummary,
    ParserAttemptRecord,
    summarize_page_extractions,
)
from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
    ParsedElement,
    ParsedElementType,
    SourceFormat,
)
from app.platform.providers.contracts.ocr import OcrImageInput, OCRProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.ocr_factory import get_ocr_provider
from app.platform.providers.implementations.parsed_element_builder import finalize_elements
from app.platform.providers.implementations.pdf_page_layout import accept_ocr_result
from app.platform.providers.implementations.pdf_page_models import PdfPageExtraction
from app.platform.providers.implementations.pdfium_page_extractor import (
    extract_pdfium_pages,
    pdfium_parser_identity,
)
from app.platform.providers.implementations.pymupdf_page_extractor import (
    extract_pymupdf_pages,
    pymupdf_parser_identity,
)

logger = structlog.get_logger(__name__)

_WORKFLOW_NAME = "pdf_extraction_workflow"
_WORKFLOW_VERSION = "1.0.0"
_KNOWN_TEXT_PARSERS = ("pymupdf", "pdfium")


@dataclass
class _PageCandidate:
    parser_id: str
    parser_version: str | None
    text: str
    elements: tuple[ParsedElement, ...]
    quality_score: float
    extraction_method: ExtractionMethod
    duration_ms: int = 0
    ocr_provider: str | None = None
    ocr_model: str | None = None
    ocr_version: str | None = None
    ocr_confidence: float | None = None


@dataclass
class _PageState:
    page_number: int
    best: _PageCandidate | None = None
    attempts: list[ParserAttemptRecord] = field(default_factory=list)


class PdfExtractionWorkflow(BaseDocumentParserProvider):
    """Orchestrate PDF text extraction with page-level quality-gated fallback."""

    def __init__(
        self,
        *,
        parsing_config: ParsingConfig | None = None,
        ocr_config: OcrConfig | None = None,
    ) -> None:
        settings = get_settings()
        self._parsing = parsing_config or settings.parsing
        self._ocr_cfg = ocr_config or settings.ocr
        self._scorer = ParseQualityScorer(
            min_page_quality_score=self._parsing.min_page_quality_score,
            min_text_chars=self._parsing.min_text_chars,
        )
        self._parser_order = _validate_parser_order(self._parsing.pdf_text_parsers)

    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        ocr_lang: str | None = None,
    ) -> ParsedDocument:
        del filename
        if content_type not in {None, "application/pdf"} and not data.startswith(b"%PDF"):
            msg = "Input is not a valid PDF document."
            raise ProviderError(msg, provider_name=_WORKFLOW_NAME)

        warnings: list[str] = []
        ocr_provider = get_ocr_provider(lang=ocr_lang) if self._ocr_cfg.enabled else None

        started = time.perf_counter()
        page_count, pymupdf_pages = extract_pymupdf_pages(data)
        pymupdf_duration_ms = int((time.perf_counter() - started) * 1000)
        per_page_pymupdf_ms = pymupdf_duration_ms // max(page_count, 1)
        pymupdf_name, pymupdf_version = pymupdf_parser_identity()

        states: dict[int, _PageState] = {
            page.page_number: _PageState(page_number=page.page_number) for page in pymupdf_pages
        }

        for page in pymupdf_pages:
            assessment = self._scorer.assess(page.text)
            candidate = _candidate_from_page(
                parser_id=pymupdf_name,
                parser_version=pymupdf_version,
                page=page,
                quality_score=assessment.score,
                duration_ms=per_page_pymupdf_ms,
                extraction_method=ExtractionMethod.NATIVE_TEXT,
            )
            _register_candidate(states[page.page_number], candidate, self._scorer)

        if "pdfium" in self._parser_order:
            degraded = _degraded_page_numbers(states, self._scorer)
            if degraded:
                started = time.perf_counter()
                _, pdfium_pages = extract_pdfium_pages(data, page_numbers=tuple(degraded))
                pdfium_duration_ms = int((time.perf_counter() - started) * 1000)
                per_page_pdfium_ms = pdfium_duration_ms // max(len(degraded), 1)
                pdfium_name, pdfium_version = pdfium_parser_identity()
                for page_number in degraded:
                    page = pdfium_pages.get(page_number)
                    text = page.text if page else ""
                    assessment = self._scorer.assess(text)
                    candidate = _candidate_from_page(
                        parser_id=pdfium_name,
                        parser_version=pdfium_version,
                        page=page,
                        quality_score=assessment.score,
                        duration_ms=per_page_pdfium_ms,
                        extraction_method=ExtractionMethod.FALLBACK_PARSER,
                    )
                    if candidate is not None:
                        _register_candidate(states[page_number], candidate, self._scorer)

        if ocr_provider is not None:
            degraded = _degraded_page_numbers(states, self._scorer)
            for page_number in degraded:
                candidate, failed_attempt = _ocr_page_candidate(
                    data=data,
                    page_number=page_number,
                    ocr_provider=ocr_provider,
                    ocr_cfg=self._ocr_cfg,
                    scorer=self._scorer,
                )
                state = states[page_number]
                if candidate is not None:
                    _register_candidate(state, candidate, self._scorer)
                elif failed_attempt is not None:
                    state.attempts.append(failed_attempt)
        else:
            for page_number in _degraded_page_numbers(states, self._scorer):
                warnings.append(f"Page {page_number}: OCR disabled and text extraction quality is low.")

        page_records = [_build_page_record(state) for state in states.values()]
        page_records.sort(key=lambda item: item.page_number)
        summary = summarize_page_extractions(tuple(page_records), total_page_count=page_count)

        if _should_fail_document_extraction(
            summary,
            ocr_enabled=self._ocr_cfg.enabled,
            min_document_success_ratio=self._parsing.min_document_success_ratio,
        ):
            msg = (
                "Document extraction failed quality threshold: "
                f"{summary.accepted_page_count}/{summary.total_page_count} pages recovered."
            )
            raise ProviderError(msg, provider_name=_WORKFLOW_NAME)

        elements = _build_elements(page_records)
        text, finalized = finalize_elements(elements)
        language_result = detect_language(text)

        if summary.partial_extraction:
            warnings.append(
                f"Partial extraction: {summary.failed_page_count + summary.empty_page_count} "
                f"of {summary.total_page_count} pages were not indexed."
            )

        structure_hints: dict[str, object] = {
            "pages": [page.to_dict() for page in page_records],
            "extraction_summary": summary.to_dict(),
            "language_confidence": language_result.confidence,
            "languages": language_result.languages,
            "is_mixed": language_result.is_mixed,
            "accepted_parser": summary.accepted_parser,
            "parse_quality_score": summary.parse_quality_score,
            "extraction_method": summary.extraction_method.value,
            "success_ratio": summary.success_ratio,
            "partial_extraction": summary.partial_extraction,
        }

        logger.info(
            "pdf_extraction_complete",
            accepted_parser=summary.accepted_parser,
            parse_quality_score=summary.parse_quality_score,
            extraction_method=summary.extraction_method.value,
            success_ratio=summary.success_ratio,
            ocr_page_count=summary.ocr_page_count,
            fallback_page_count=summary.fallback_page_count,
            partial_extraction=summary.partial_extraction,
        )

        return ParsedDocument(
            text=text,
            page_count=page_count,
            parser_name=summary.accepted_parser,
            parser_version=_WORKFLOW_VERSION,
            elements=finalized,
            source_format=SourceFormat.PDF,
            parser_confidence=summary.parser_confidence,
            parse_quality_score=summary.parse_quality_score,
            ocr_quality=summary.ocr_quality,
            language=language_result.primary_language,
            warnings=tuple(warnings),
            structure_hints=structure_hints,
        )

    @staticmethod
    def supports(filename: str, content_type: str | None) -> bool:
        lower = filename.lower()
        if lower.endswith(".pdf"):
            return True
        return content_type == "application/pdf"


def _should_fail_document_extraction(
    summary: ParseQualitySummary,
    *,
    ocr_enabled: bool,
    min_document_success_ratio: float,
) -> bool:
    """Fail only when nothing is recoverable, or OCR was expected to fill gaps."""
    if summary.accepted_page_count == 0:
        return True
    if not ocr_enabled:
        return False
    return summary.success_ratio < min_document_success_ratio


def _validate_parser_order(parser_ids: list[str]) -> tuple[str, ...]:
    normalized = [item.strip().lower() for item in parser_ids if item.strip()]
    if not normalized:
        normalized = ["pymupdf", "pdfium"]
    unknown = [item for item in normalized if item not in _KNOWN_TEXT_PARSERS]
    if unknown:
        msg = f"Unsupported PDF text parser(s): {', '.join(unknown)}"
        raise ProviderError(msg, provider_name=_WORKFLOW_NAME)
    deduped: list[str] = []
    for item in normalized:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def _candidate_from_page(
    *,
    parser_id: str,
    parser_version: str,
    page: PdfPageExtraction | None,
    quality_score: float,
    duration_ms: int,
    extraction_method: ExtractionMethod,
) -> _PageCandidate | None:
    if page is None:
        return None
    return _PageCandidate(
        parser_id=parser_id,
        parser_version=parser_version,
        text=page.text,
        elements=page.elements,
        quality_score=quality_score,
        extraction_method=extraction_method,
        duration_ms=duration_ms,
    )


def _register_candidate(
    state: _PageState,
    candidate: _PageCandidate,
    scorer: ParseQualityScorer,
) -> None:
    if state.best is None:
        state.best = candidate
        state.attempts.append(_attempt_from_candidate(candidate, CandidateSelectionStatus.SELECTED))
        return

    if candidate.quality_score > state.best.quality_score:
        state.attempts.append(
            _attempt_from_candidate(
                state.best,
                CandidateSelectionStatus.REJECTED,
                rejection_reason="lower_quality_than_new_candidate",
            )
        )
        state.best = candidate
        state.attempts.append(_attempt_from_candidate(candidate, CandidateSelectionStatus.SELECTED))
        return

    state.attempts.append(
        _attempt_from_candidate(
            candidate,
            CandidateSelectionStatus.REJECTED,
            rejection_reason="lower_quality_than_existing_candidate",
        )
    )


def _attempt_from_candidate(
    candidate: _PageCandidate,
    status: CandidateSelectionStatus,
    *,
    rejection_reason: str | None = None,
) -> ParserAttemptRecord:
    return ParserAttemptRecord(
        parser_id=candidate.parser_id,
        parser_version=candidate.parser_version,
        duration_ms=candidate.duration_ms,
        parse_quality_score=candidate.quality_score,
        selection_status=status,
        rejection_reason=rejection_reason,
        ocr_provider=candidate.ocr_provider,
        ocr_model=candidate.ocr_model,
        ocr_version=candidate.ocr_version,
        ocr_confidence=candidate.ocr_confidence,
    )


def _degraded_page_numbers(states: dict[int, _PageState], scorer: ParseQualityScorer) -> list[int]:
    degraded: list[int] = []
    for page_number, state in sorted(states.items()):
        if state.best is None:
            degraded.append(page_number)
            continue
        if not scorer.assess(state.best.text).is_acceptable:
            degraded.append(page_number)
    return degraded


def _build_page_record(state: _PageState) -> PageExtractionRecord:
    if state.best is None or not state.best.text.strip():
        return PageExtractionRecord(
            page_number=state.page_number,
            text="",
            status=PageExtractionStatus.UNRECOVERABLE,
            parse_quality_score=0.0,
            accepted_parser="none",
            extraction_method=ExtractionMethod.NATIVE_TEXT,
            attempts=tuple(state.attempts),
            char_count=0,
        )

    best = state.best
    assessment_ok = ParseQualityScorer().assess(best.text).is_acceptable
    if not assessment_ok:
        return PageExtractionRecord(
            page_number=state.page_number,
            text="",
            status=PageExtractionStatus.UNRECOVERABLE,
            parse_quality_score=best.quality_score,
            accepted_parser=best.parser_id,
            extraction_method=best.extraction_method,
            attempts=tuple(state.attempts),
            char_count=0,
        )

    status = PageExtractionStatus.ACCEPTED
    if best.extraction_method is ExtractionMethod.FALLBACK_PARSER:
        status = PageExtractionStatus.FALLBACK_PARSER_USED
    elif best.extraction_method is ExtractionMethod.OCR:
        status = PageExtractionStatus.OCR_USED

    text = best.text.strip()
    return PageExtractionRecord(
        page_number=state.page_number,
        text=text,
        status=status,
        parse_quality_score=best.quality_score,
        accepted_parser=best.parser_id,
        extraction_method=best.extraction_method,
        attempts=tuple(state.attempts),
        char_count=len(text),
    )


def _build_elements(page_records: list[PageExtractionRecord]) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    accepted = [
        record
        for record in page_records
        if record.text.strip()
        and record.status
        in {
            PageExtractionStatus.ACCEPTED,
            PageExtractionStatus.FALLBACK_PARSER_USED,
            PageExtractionStatus.OCR_USED,
        }
    ]
    for index, record in enumerate(accepted):
        paragraphs = [part.strip() for part in record.text.split("\n\n") if part.strip()]
        if not paragraphs:
            paragraphs = [record.text.strip()]
        for paragraph in paragraphs:
            metadata: dict[str, object] = {
                "parse_quality_score": record.parse_quality_score,
                "extraction_method": record.extraction_method.value,
                "accepted_parser": record.accepted_parser,
            }
            for attempt in record.attempts:
                if attempt.selection_status is CandidateSelectionStatus.SELECTED and attempt.ocr_confidence is not None:
                    metadata["ocr_confidence"] = attempt.ocr_confidence
                    if attempt.ocr_provider is not None:
                        metadata["ocr_source"] = attempt.ocr_provider
            elements.append(
                ParsedElement(
                    text=normalize_for_storage(paragraph),
                    element_type=ParsedElementType.PARAGRAPH,
                    page_start=record.page_number,
                    page_end=record.page_number,
                    metadata=metadata,
                )
            )
        if index < len(accepted) - 1:
            elements.append(
                ParsedElement(
                    text="",
                    element_type=ParsedElementType.PAGE_BREAK,
                    page_start=record.page_number,
                    page_end=record.page_number,
                )
            )
    return elements


def _ocr_page_candidate(
    *,
    data: bytes,
    page_number: int,
    ocr_provider: OCRProvider,
    ocr_cfg: OcrConfig,
    scorer: ParseQualityScorer,
) -> tuple[_PageCandidate | None, ParserAttemptRecord | None]:
    started = time.perf_counter()
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            page = document[page_number - 1]
            pixmap = page.get_pixmap(dpi=ocr_cfg.dpi)
        result = ocr_provider.recognize(
            OcrImageInput(
                data=pixmap.tobytes("png"),
                mime_type="image/png",
                page_number=page_number,
            )
        )
    except ProviderError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return None, ParserAttemptRecord(
            parser_id="ocr",
            parser_version=None,
            duration_ms=duration_ms,
            parse_quality_score=0.0,
            selection_status=CandidateSelectionStatus.REJECTED,
            rejection_reason=str(exc.message),
            ocr_provider=ocr_provider.provider_name,
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    assessment = scorer.assess(result.text)
    ocr_accepted = accept_ocr_result(
        result.text,
        result.confidence,
        min_text_chars=ocr_cfg.min_text_chars,
        min_confidence=ocr_cfg.min_page_confidence,
    )
    quality_score = assessment.score if ocr_accepted else min(assessment.score, 0.2)
    ocr_model, ocr_version = _ocr_provenance(ocr_provider)
    candidate = _PageCandidate(
        parser_id="ocr",
        parser_version=None,
        text=result.text,
        elements=tuple(
            ParsedElement(
                text=normalize_for_storage(paragraph),
                element_type=ParsedElementType.PARAGRAPH,
                page_start=page_number,
                page_end=page_number,
                metadata={
                    "ocr_confidence": result.confidence,
                    "ocr_source": result.provider_name,
                    "content_source": "ocr_page",
                },
            )
            for paragraph in [part.strip() for part in result.text.split("\n\n") if part.strip()]
            or ([result.text.strip()] if result.text.strip() else [])
        ),
        quality_score=quality_score,
        extraction_method=ExtractionMethod.OCR,
        duration_ms=duration_ms,
        ocr_provider=result.provider_name,
        ocr_model=ocr_model,
        ocr_version=ocr_version,
        ocr_confidence=result.confidence,
    )
    attempt = ParserAttemptRecord(
        parser_id="ocr",
        parser_version=None,
        duration_ms=duration_ms,
        parse_quality_score=quality_score,
        selection_status=CandidateSelectionStatus.REJECTED,
        rejection_reason=None,
        ocr_provider=result.provider_name,
        ocr_model=ocr_model,
        ocr_version=ocr_version,
        ocr_confidence=result.confidence,
    )
    return candidate, None


def _ocr_provenance(ocr_provider: OCRProvider) -> tuple[str | None, str | None]:
    provider_name = ocr_provider.provider_name
    if provider_name == "paddle":
        try:
            import paddleocr  # type: ignore[import-untyped]

            return "paddleocr", getattr(paddleocr, "__version__", None)
        except ImportError:
            return "paddleocr", None
    return provider_name, None
