"""Unit tests for PDF extraction workflow."""

from __future__ import annotations

from dataclasses import dataclass

import fitz
import pytest

from app.core.config import OcrConfig, ParsingConfig
from app.platform.domain.parse_quality import CandidateSelectionStatus, ExtractionMethod
from app.platform.providers.implementations.pdf_extraction_workflow import PdfExtractionWorkflow

pytestmark = pytest.mark.unit


def _minimal_pdf(text: str = "PDF text") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def test_pdf_extraction_workflow_accepts_good_pdf() -> None:
    workflow = PdfExtractionWorkflow(
        parsing_config=ParsingConfig(min_page_quality_score=0.55, min_document_success_ratio=0.2),
    )
    result = workflow.parse(
        data=_minimal_pdf("Budget allocation for fiscal year 2026-27."),
        filename="sample.pdf",
        content_type="application/pdf",
    )
    assert result.page_count == 1
    assert "Budget allocation" in result.text
    assert result.parse_quality_score is not None
    assert result.parse_quality_score >= 0.55
    assert result.structure_hints["accepted_parser"] == "pymupdf"


def test_pdf_extraction_workflow_accepts_partial_success_when_ocr_disabled() -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Readable budget allocation for fiscal year 2026-27.")
    page = document.new_page()
    page.insert_text((72, 72), "\x01\x02\x03" * 40)
    pdf_bytes = document.tobytes()
    document.close()

    workflow = PdfExtractionWorkflow(
        parsing_config=ParsingConfig(
            min_page_quality_score=0.55,
            min_document_success_ratio=0.5,
            pdf_text_parsers=["pymupdf"],
        ),
        ocr_config=OcrConfig(enabled=False),
    )
    result = workflow.parse(
        data=pdf_bytes,
        filename="partial.pdf",
        content_type="application/pdf",
    )
    assert result.page_count == 2
    assert "Readable budget allocation" in result.text
    assert result.structure_hints["partial_extraction"] is True
    assert result.structure_hints["success_ratio"] == 0.5


def test_pdf_extraction_workflow_fails_when_all_pages_unrecoverable() -> None:
    workflow = PdfExtractionWorkflow(
        parsing_config=ParsingConfig(
            min_page_quality_score=0.99,
            min_document_success_ratio=0.5,
            pdf_text_parsers=["pymupdf"],
        ),
        ocr_config=OcrConfig(enabled=False),
    )
    with pytest.raises(Exception):
        workflow.parse(
            data=_minimal_pdf("Short"),
            filename="sample.pdf",
            content_type="application/pdf",
        )


@dataclass
class _FakeOcrProvider:
    text: str
    confidence: float = 0.95

    @property
    def provider_name(self) -> str:
        return "fake_ocr"

    def recognize(self, image):  # noqa: ANN001
        from app.platform.providers.contracts.ocr import OcrPageResult

        return OcrPageResult(
            text=self.text,
            confidence=self.confidence,
            provider_name=self.provider_name,
            page_number=image.page_number,
        )


def test_register_candidate_keeps_highest_quality_extraction() -> None:
    from app.platform.domain.parse_quality import ParseQualityScorer
    from app.platform.providers.implementations.pdf_extraction_workflow import (
        _PageCandidate,
        _PageState,
        _register_candidate,
    )

    state = _PageState(page_number=1)
    scorer = ParseQualityScorer(min_page_quality_score=0.55, min_text_chars=20)
    parser_candidate = _PageCandidate(
        parser_id="pymupdf",
        parser_version="1.0.0",
        text="Readable budget allocation for fiscal year 2026-27.",
        elements=(),
        quality_score=0.82,
        extraction_method=ExtractionMethod.NATIVE_TEXT,
    )
    ocr_candidate = _PageCandidate(
        parser_id="ocr",
        parser_version=None,
        text="\x01\x02\x03" * 30,
        elements=(),
        quality_score=0.12,
        extraction_method=ExtractionMethod.OCR,
        ocr_provider="fake_ocr",
        ocr_confidence=0.1,
    )
    _register_candidate(state, parser_candidate, scorer)
    _register_candidate(state, ocr_candidate, scorer)
    assert state.best is not None
    assert state.best.parser_id == "pymupdf"
    assert any(
        attempt.selection_status is CandidateSelectionStatus.REJECTED and attempt.parser_id == "ocr"
        for attempt in state.attempts
    )


def test_pdf_extraction_workflow_records_parser_attempts() -> None:
    workflow = PdfExtractionWorkflow(
        parsing_config=ParsingConfig(min_page_quality_score=0.55, min_document_success_ratio=0.2),
    )
    result = workflow.parse(
        data=_minimal_pdf("Readable budget allocation for fiscal year 2026-27."),
        filename="sample.pdf",
        content_type="application/pdf",
    )
    pages = result.structure_hints["pages"]
    assert pages
    assert pages[0]["attempts"]
    assert pages[0]["attempts"][0]["duration_ms"] >= 0
    assert pages[0]["attempts"][0]["selection_status"] == CandidateSelectionStatus.SELECTED.value
