"""PDFium native page extraction via pypdfium2."""

from __future__ import annotations

import pypdfium2 as pdfium

from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.document_parser import ParsedElement, ParsedElementType
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.pdf_page_models import PdfPageExtraction

_PARSER_NAME = "pdfium"
_PARSER_VERSION = getattr(pdfium, "VLibpdfium", pdfium).get_version() if hasattr(
    getattr(pdfium, "VLibpdfium", pdfium), "get_version"
) else "bundled"


def extract_pdfium_pages(
    data: bytes,
    *,
    page_numbers: tuple[int, ...] | None = None,
) -> tuple[int, dict[int, PdfPageExtraction]]:
    """Extract native text for selected pages (1-based page numbers)."""
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        msg = "Failed to open PDF document with PDFium."
        raise ProviderError(msg, provider_name=_PARSER_NAME) from exc

    try:
        total_pages = len(document)
        targets = page_numbers or tuple(range(1, total_pages + 1))
        pages: dict[int, PdfPageExtraction] = {}
        for page_number in targets:
            if page_number < 1 or page_number > total_pages:
                continue
            page = document[page_number - 1]
            textpage = page.get_textpage()
            try:
                raw_text = textpage.get_text_bounded()
            finally:
                textpage.close()
            page_text = normalize_for_storage(raw_text)
            elements = tuple(
                ParsedElement(
                    text=normalize_for_storage(paragraph),
                    element_type=ParsedElementType.PARAGRAPH,
                    page_start=page_number,
                    page_end=page_number,
                )
                for paragraph in _paragraphs_from_text(page_text)
            )
            pages[page_number] = PdfPageExtraction(
                page_number=page_number,
                text=page_text,
                elements=elements,
            )
        return total_pages, pages
    except Exception as exc:
        msg = "Failed to parse PDF document with PDFium."
        raise ProviderError(msg, provider_name=_PARSER_NAME) from exc
    finally:
        document.close()


def pdfium_parser_identity() -> tuple[str, str]:
    return _PARSER_NAME, _PARSER_VERSION


def _paragraphs_from_text(page_text: str) -> list[str]:
    paragraphs = [part.strip() for part in page_text.split("\n\n") if part.strip()]
    if not paragraphs and page_text.strip():
        paragraphs = [line.strip() for line in page_text.splitlines() if line.strip()]
    return paragraphs
