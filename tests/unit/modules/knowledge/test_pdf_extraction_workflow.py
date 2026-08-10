"""Unit tests for PDF extraction workflow."""

from __future__ import annotations

from dataclasses import dataclass

import fitz
import pytest

from app.core.config import OcrBackend, OcrConfig, ParsingConfig
from app.platform.domain.parse_quality import CandidateSelectionStatus, ExtractionMethod
from app.platform.providers.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderTimeoutError,
)
from app.platform.providers.implementations.pdf_extraction_workflow import PdfExtractionWorkflow

pytestmark = pytest.mark.unit


def _minimal_pdf(text: str = "PDF text") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def _minimal_pdf_pages(page_count: int) -> bytes:
    document = fitz.open()
    for page_number in range(1, page_count + 1):
        page = document.new_page()
        page.insert_text((72, 72), f"Native page {page_number} with readable English text.")
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
    with pytest.raises(ProviderError):
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

    def recognize(self, image):
        from app.platform.providers.contracts.ocr import OcrPageResult

        return OcrPageResult(
            text=self.text,
            confidence=self.confidence,
            provider_name=self.provider_name,
            page_number=image.page_number,
        )


class _RecordingBanglaOcrProvider:
    provider_name = "google_vision"

    def __init__(self) -> None:
        self.pages: list[int | None] = []

    def recognize(self, image):
        from app.platform.providers.contracts.ocr import OcrPageResult

        self.pages.append(image.page_number)
        return OcrPageResult(
            text=f"বাংলা নথির পৃষ্ঠা {image.page_number} থেকে সঠিকভাবে লেখা উদ্ধার করা হয়েছে।",
            confidence=0.96,
            provider_name=self.provider_name,
            page_number=image.page_number,
        )


def _bangla_ocr_config(**updates: object) -> OcrConfig:
    return OcrConfig(
        enabled=True,
        backend=OcrBackend.NOOP,
        bangla_backend=OcrBackend.GOOGLE_VISION,
        google_api_key="test-key",
        **updates,
    )


def test_bangla_route_ocr_every_page_and_skips_native_and_pdfium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingBanglaOcrProvider()
    monkeypatch.setattr(
        "app.platform.providers.implementations.pdf_extraction_workflow.get_ocr_provider",
        lambda **_kwargs: provider,
    )

    def fail_pdfium(*_args, **_kwargs):
        raise AssertionError("PDFium must not run on the Bangla OCR-first route")

    monkeypatch.setattr(
        "app.platform.providers.implementations.pdf_extraction_workflow.extract_pdfium_pages",
        fail_pdfium,
    )
    workflow = PdfExtractionWorkflow(ocr_config=_bangla_ocr_config())

    result = workflow.parse(
        data=_minimal_pdf_pages(2),
        filename="bangla.pdf",
        content_type="application/pdf",
        ocr_lang="bn",
    )

    assert provider.pages == [1, 2]
    assert result.parser_name == "ocr"
    assert result.structure_hints["accepted_parser"] == "ocr"
    assert result.structure_hints["extraction_method"] == ExtractionMethod.OCR.value
    assert result.structure_hints["language_routing"] == {
        "resolved_language": "bn",
        "source": "explicit",
        "ocr_first": True,
    }
    assert all(page["accepted_parser"] == "ocr" for page in result.structure_hints["pages"])
    selected_attempt = result.structure_hints["pages"][0]["attempts"][0]
    assert selected_attempt["ocr_model"] == "google_cloud_vision"
    assert selected_attempt["ocr_version"] == "v1"


def test_bangla_route_auto_detects_unicode_text_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.platform.providers.implementations.pdf_page_models import PdfPageExtraction

    provider = _RecordingBanglaOcrProvider()
    monkeypatch.setattr(
        "app.platform.providers.implementations.pdf_extraction_workflow.get_ocr_provider",
        lambda **_kwargs: provider,
    )
    monkeypatch.setattr(
        "app.platform.providers.implementations.pdf_extraction_workflow.extract_pymupdf_pages",
        lambda _data: (
            1,
            (
                PdfPageExtraction(
                    page_number=1,
                    text="এটি একটি ইউনিকোড বাংলা নথির নির্ভরযোগ্য নমুনা লেখা।",
                ),
            ),
        ),
    )
    workflow = PdfExtractionWorkflow(ocr_config=_bangla_ocr_config())

    result = workflow.parse(
        data=_minimal_pdf_pages(1),
        filename="bangla.pdf",
        content_type="application/pdf",
    )

    assert provider.pages == [1]
    assert result.structure_hints["language_routing"] == {
        "resolved_language": "bn",
        "source": "detected",
        "ocr_first": True,
    }


def test_bangla_route_propagates_retryable_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutProvider:
        provider_name = "google_vision"

        def recognize(self, _image):
            raise ProviderTimeoutError("timed out", provider_name=self.provider_name)

    monkeypatch.setattr(
        "app.platform.providers.implementations.pdf_extraction_workflow.get_ocr_provider",
        lambda **_kwargs: TimeoutProvider(),
    )
    workflow = PdfExtractionWorkflow(ocr_config=_bangla_ocr_config())

    with pytest.raises(ProviderTimeoutError):
        workflow.parse(
            data=_minimal_pdf_pages(1),
            filename="bangla.pdf",
            content_type="application/pdf",
            ocr_lang="bn",
        )


def test_bangla_route_propagates_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AuthenticationFailureProvider:
        provider_name = "google_vision"

        def recognize(self, _image):
            raise ProviderAuthenticationError(
                "invalid credential",
                provider_name=self.provider_name,
            )

    monkeypatch.setattr(
        "app.platform.providers.implementations.pdf_extraction_workflow.get_ocr_provider",
        lambda **_kwargs: AuthenticationFailureProvider(),
    )
    workflow = PdfExtractionWorkflow(ocr_config=_bangla_ocr_config())

    with pytest.raises(ProviderAuthenticationError, match="invalid credential"):
        workflow.parse(
            data=_minimal_pdf_pages(1),
            filename="bangla.pdf",
            content_type="application/pdf",
            ocr_lang="bn",
        )


def test_bangla_route_fails_when_page_cap_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.platform.providers.implementations.pdf_extraction_workflow.get_ocr_provider",
        lambda **_kwargs: _RecordingBanglaOcrProvider(),
    )
    workflow = PdfExtractionWorkflow(
        ocr_config=_bangla_ocr_config(max_ocr_pages_per_document=1)
    )

    with pytest.raises(ProviderError, match="exceeding the configured limit"):
        workflow.parse(
            data=_minimal_pdf_pages(2),
            filename="bangla.pdf",
            content_type="application/pdf",
            ocr_lang="bn",
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
