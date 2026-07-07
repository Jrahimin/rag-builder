"""PyMuPDF native page extraction without OCR."""

from __future__ import annotations

import fitz

from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.document_parser import ParsedElement, ParsedElementType
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.pdf_page_layout import analyze_page_blocks
from app.platform.providers.implementations.pdf_page_models import PdfPageExtraction

_PARSER_NAME = "pymupdf"
_PARSER_VERSION = "1.26.3"


def extract_pymupdf_pages(data: bytes) -> tuple[int, tuple[PdfPageExtraction, ...]]:
    """Extract native text for every page in a PDF."""
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            pages: list[PdfPageExtraction] = []
            for page in document:
                page_number = page.number + 1
                page_rect = page.rect
                page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                blocks = page_dict.get("blocks", [])
                layout = analyze_page_blocks(
                    blocks,
                    page_width=float(page_rect.width),
                    page_height=float(page_rect.height),
                    min_image_area_ratio=1.0,
                )
                page_text = normalize_for_storage(page.get_text())
                page_median_size = _page_median_font_size_from_blocks(layout.text_blocks)
                elements = _blocks_to_elements(
                    layout,
                    page_number,
                    page_median_size=page_median_size,
                )
                if not elements and page_text:
                    elements = tuple(
                        ParsedElement(
                            text=normalize_for_storage(paragraph),
                            element_type=ParsedElementType.PARAGRAPH,
                            page_start=page_number,
                            page_end=page_number,
                        )
                        for paragraph in _paragraphs_from_text(page_text)
                    )
                pages.append(
                    PdfPageExtraction(
                        page_number=page_number,
                        text=page_text,
                        elements=elements,
                        layout_complex=layout.is_complex,
                        metadata={
                            "text_block_count": layout.text_block_count,
                            "image_block_count": layout.image_block_count,
                        },
                    )
                )
            return document.page_count, tuple(pages)
    except Exception as exc:
        msg = "Failed to parse PDF document with PyMuPDF."
        raise ProviderError(msg, provider_name=_PARSER_NAME) from exc


def pymupdf_parser_identity() -> tuple[str, str]:
    return _PARSER_NAME, _PARSER_VERSION


def _paragraphs_from_text(page_text: str) -> list[str]:
    paragraphs = [part.strip() for part in page_text.split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [line.strip() for line in page_text.splitlines() if line.strip()]
    return paragraphs


def _page_median_font_size_from_blocks(text_blocks: tuple[dict, ...]) -> float | None:
    sizes: list[float] = []
    for block in text_blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size")
                if size:
                    sizes.append(float(size))
    if not sizes:
        return None
    sizes.sort()
    return sizes[len(sizes) // 2]


def _blocks_to_elements(
    layout,
    page_number: int,
    *,
    page_median_size: float | None,
) -> tuple[ParsedElement, ...]:
    elements: list[ParsedElement] = []
    for block in layout.text_blocks:
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = normalize_for_storage("".join(str(span.get("text", "")) for span in spans))
            if not text:
                continue
            max_size = max((float(span.get("size", 0)) for span in spans), default=0.0)
            is_bold = any("bold" in str(span.get("font", "")).lower() for span in spans)
            element_type = _detect_heading(
                text,
                font_size=max_size,
                page_median_size=page_median_size,
                is_bold=is_bold,
            )
            metadata: dict[str, object] = {}
            if layout.is_complex:
                metadata["layout_complex"] = True
            elements.append(
                ParsedElement(
                    text=text,
                    element_type=element_type,
                    page_start=page_number,
                    page_end=page_number,
                    heading_level=_heading_level(max_size, page_median_size)
                    if element_type is ParsedElementType.HEADING
                    else None,
                    metadata=metadata,
                )
            )
    return tuple(elements)


def _detect_heading(
    text: str,
    *,
    font_size: float,
    page_median_size: float | None,
    is_bold: bool,
) -> ParsedElementType:
    if len(text) > 160 or text.endswith("."):
        return ParsedElementType.PARAGRAPH
    if page_median_size and font_size >= page_median_size * 1.2:
        return ParsedElementType.HEADING
    if is_bold and len(text.split()) <= 12:
        return ParsedElementType.HEADING
    return ParsedElementType.PARAGRAPH


def _heading_level(font_size: float, page_median_size: float | None) -> int:
    if page_median_size and font_size >= page_median_size * 1.5:
        return 1
    return 2
