"""PDF text extraction via PyMuPDF (fitz)."""

from __future__ import annotations

import fitz
from typing_extensions import TypedDict

from app.core.config import OcrConfig, get_settings
from app.platform.domain.language_detection import detect_language
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
from app.platform.providers.implementations.pdf_page_layout import (
    PageLayout,
    accept_ocr_result,
    analyze_page_blocks,
)

_PARSER_NAME = "pymupdf"
_PARSER_VERSION = "1.26.3"


class _OcrResult(TypedDict):
    """Typed intermediate result shared by page and region OCR paths."""

    text: str
    confidence: float
    source: str
    accepted: bool
    meta: dict[str, object]


class PyMuPDFParserProvider(BaseDocumentParserProvider):
    """Extract structured text from digital PDFs using PyMuPDF."""

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
            raise ProviderError(msg, provider_name=_PARSER_NAME)

        settings = get_settings()
        ocr_cfg = settings.ocr
        ocr_provider = get_ocr_provider(lang=ocr_lang) if ocr_cfg.enabled else None

        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                elements: list[ParsedElement] = []
                warnings: list[str] = []
                image_only_pages = 0
                sparse_pages = 0
                ocr_pages: list[dict[str, object]] = []
                skipped_ocr_regions = 0
                ocr_image_regions = 0
                complex_page_count = 0

                for page in document:
                    page_number = page.number + 1
                    page_rect = page.rect
                    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                    blocks = page_dict.get("blocks", [])
                    layout = analyze_page_blocks(
                        blocks,
                        page_width=float(page_rect.width),
                        page_height=float(page_rect.height),
                        min_image_area_ratio=ocr_cfg.min_image_area_ratio,
                    )
                    if layout.is_complex:
                        complex_page_count += 1

                    page_text = normalize_for_storage(page.get_text())
                    page_median_size = _page_median_font_size_from_blocks(layout.text_blocks)

                    if not page_text:
                        image_only_pages += _process_image_only_page(
                            page=page,
                            page_number=page_number,
                            elements=elements,
                            warnings=warnings,
                            ocr_pages=ocr_pages,
                            ocr_provider=ocr_provider,
                            ocr_cfg=ocr_cfg,
                        )
                        _append_page_break(elements, page_number, document.page_count)
                        continue

                    page_elements = _blocks_to_elements(
                        layout,
                        page_number,
                        page_median_size=page_median_size,
                    )
                    ocr_pages.append(
                        {
                            "page": page_number,
                            "confidence": 1.0,
                            "source": "native",
                            "layout_complex": layout.is_complex,
                        }
                    )
                    if page_elements:
                        elements.extend(page_elements)
                    else:
                        sparse_pages += _append_fallback_paragraphs(
                            elements,
                            page_text,
                            page_number,
                        )

                    if ocr_provider is not None and layout.image_regions:
                        added, region_meta, skipped = _ocr_large_image_regions(
                            page=page,
                            page_number=page_number,
                            layout=layout,
                            ocr_provider=ocr_provider,
                            ocr_cfg=ocr_cfg,
                        )
                        elements.extend(added)
                        ocr_pages.extend(region_meta)
                        skipped_ocr_regions += skipped
                        ocr_image_regions += len(region_meta)

                    _append_page_break(elements, page_number, document.page_count)

                page_count = document.page_count
        except Exception as exc:
            msg = "Failed to parse PDF document."
            raise ProviderError(msg, provider_name=_PARSER_NAME) from exc

        text, finalized = finalize_elements(elements)
        confidence = _compute_confidence(
            page_count=page_count,
            image_only_pages=image_only_pages,
            sparse_pages=sparse_pages,
            element_count=len(finalized),
        )
        ocr_quality = None
        min_page_confidence = None
        if ocr_pages:
            confidences = [
                float(value)
                for item in ocr_pages
                if isinstance((value := item.get("confidence")), (int, float))
            ]
            if confidences:
                min_page_confidence = min(confidences)
                ocr_quality = sum(confidences) / len(confidences)
        elif image_only_pages:
            ocr_quality = max(0.0, 1.0 - image_only_pages / max(page_count, 1))

        language_result = detect_language(text)
        structure_hints: dict[str, object] = {
            "image_only_pages": image_only_pages,
            "sparse_pages": sparse_pages,
            "ocr_pages": ocr_pages,
            "ocr_page_count": sum(1 for item in ocr_pages if item.get("source") != "native"),
            "ocr_image_regions": ocr_image_regions,
            "skipped_ocr_regions": skipped_ocr_regions,
            "complex_page_count": complex_page_count,
            "min_page_confidence": min_page_confidence,
            "language_confidence": language_result.confidence,
            "languages": language_result.languages,
            "is_mixed": language_result.is_mixed,
        }

        return ParsedDocument(
            text=text,
            page_count=page_count,
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
            elements=finalized,
            source_format=SourceFormat.PDF,
            parser_confidence=confidence,
            ocr_quality=ocr_quality,
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


def _append_page_break(
    elements: list[ParsedElement],
    page_number: int,
    page_count: int,
) -> None:
    if page_number < page_count:
        elements.append(
            ParsedElement(
                text="",
                element_type=ParsedElementType.PAGE_BREAK,
                page_start=page_number,
                page_end=page_number,
            )
        )


def _process_image_only_page(
    *,
    page: fitz.Page,
    page_number: int,
    elements: list[ParsedElement],
    warnings: list[str],
    ocr_pages: list[dict[str, object]],
    ocr_provider: OCRProvider | None,
    ocr_cfg: OcrConfig,
) -> int:
    """Handle pages with no native text. Returns 1 if counted as image-only."""
    if page.get_images() and ocr_provider is not None:
        ocr_result = _ocr_page(
            page,
            ocr_provider=ocr_provider,
            dpi=ocr_cfg.dpi,
            page_number=page_number,
            ocr_cfg=ocr_cfg,
        )
        if ocr_result is not None:
            ocr_pages.append(ocr_result["meta"])
            if ocr_result["accepted"]:
                elements.extend(
                    _paragraph_elements(
                        ocr_result["text"],
                        page_number,
                        ocr_confidence=ocr_result["confidence"],
                        ocr_source=ocr_result["source"],
                        content_source="ocr_page",
                    )
                )
                return 0
            warnings.append(f"Page {page_number} OCR below min_text_chars or min_page_confidence.")
            return 1
    if page.get_images():
        ocr_pages.append({"page": page_number, "confidence": 0.0, "source": "native"})
        warnings.append(f"Page {page_number} contains images only; OCR is not enabled.")
        return 1
    return 0


def _append_fallback_paragraphs(
    elements: list[ParsedElement],
    page_text: str,
    page_number: int,
) -> int:
    """Append paragraph elements from raw page text. Returns 1 if page is sparse."""
    paragraphs = [part.strip() for part in page_text.split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [line.strip() for line in page_text.splitlines() if line.strip()]
    sparse = 1 if len(paragraphs) <= 1 and len(page_text) > 1200 else 0
    for paragraph in paragraphs:
        elements.append(
            ParsedElement(
                text=normalize_for_storage(paragraph),
                element_type=ParsedElementType.PARAGRAPH,
                page_start=page_number,
                page_end=page_number,
            )
        )
    return sparse


def _ocr_large_image_regions(
    *,
    page: fitz.Page,
    page_number: int,
    layout: PageLayout,
    ocr_provider: OCRProvider,
    ocr_cfg: OcrConfig,
) -> tuple[list[ParsedElement], list[dict[str, object]], int]:
    elements: list[ParsedElement] = []
    meta: list[dict[str, object]] = []
    skipped = 0
    for region in layout.image_regions:
        ocr_result = _ocr_bbox(
            page,
            bbox=region.bbox,
            ocr_provider=ocr_provider,
            dpi=ocr_cfg.dpi,
            page_number=page_number,
            ocr_cfg=ocr_cfg,
            area_ratio=region.area_ratio,
        )
        if ocr_result is None or not ocr_result["accepted"]:
            skipped += 1
            continue
        meta.append(ocr_result["meta"])
        elements.extend(
            _paragraph_elements(
                ocr_result["text"],
                page_number,
                ocr_confidence=ocr_result["confidence"],
                ocr_source=ocr_result["source"],
                content_source="ocr_image",
                area_ratio=region.area_ratio,
            )
        )
    return elements, meta, skipped


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
    layout: PageLayout,
    page_number: int,
    *,
    page_median_size: float | None,
) -> list[ParsedElement]:
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
    return elements


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


def _paragraph_elements(
    text: str,
    page_number: int,
    *,
    ocr_confidence: float,
    ocr_source: str,
    content_source: str = "ocr_page",
    area_ratio: float | None = None,
) -> list[ParsedElement]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()] if text.strip() else []
    elements: list[ParsedElement] = []
    for paragraph in paragraphs:
        metadata: dict[str, object] = {
            "ocr_confidence": ocr_confidence,
            "ocr_source": ocr_source,
            "content_source": content_source,
        }
        if area_ratio is not None:
            metadata["image_area_ratio"] = round(area_ratio, 4)
        elements.append(
            ParsedElement(
                text=normalize_for_storage(paragraph),
                element_type=ParsedElementType.PARAGRAPH,
                page_start=page_number,
                page_end=page_number,
                metadata=metadata,
            )
        )
    return elements


def _ocr_page(
    page: fitz.Page,
    *,
    ocr_provider: OCRProvider,
    dpi: int,
    page_number: int,
    ocr_cfg: OcrConfig,
) -> _OcrResult | None:
    try:
        pixmap = page.get_pixmap(dpi=dpi)
        result = ocr_provider.recognize(
            OcrImageInput(
                data=pixmap.tobytes("png"),
                mime_type="image/png",
                page_number=page_number,
            )
        )
    except ProviderError:
        return None
    accepted = accept_ocr_result(
        result.text,
        result.confidence,
        min_text_chars=ocr_cfg.min_text_chars,
        min_confidence=ocr_cfg.min_page_confidence,
    )
    return {
        "text": result.text,
        "confidence": result.confidence,
        "source": result.provider_name,
        "accepted": accepted,
        "meta": {
            "page": page_number,
            "confidence": result.confidence,
            "source": result.provider_name,
            "scope": "page",
            "accepted": accepted,
        },
    }


def _ocr_bbox(
    page: fitz.Page,
    *,
    bbox: tuple[float, float, float, float],
    ocr_provider: OCRProvider,
    dpi: int,
    page_number: int,
    ocr_cfg: OcrConfig,
    area_ratio: float,
) -> _OcrResult | None:
    try:
        clip = fitz.Rect(bbox)
        pixmap = page.get_pixmap(dpi=dpi, clip=clip)
        result = ocr_provider.recognize(
            OcrImageInput(
                data=pixmap.tobytes("png"),
                mime_type="image/png",
                page_number=page_number,
                metadata={"area_ratio": area_ratio},
            )
        )
    except ProviderError:
        return None
    accepted = accept_ocr_result(
        result.text,
        result.confidence,
        min_text_chars=ocr_cfg.min_text_chars,
        min_confidence=ocr_cfg.min_page_confidence,
    )
    return {
        "text": result.text,
        "confidence": result.confidence,
        "source": result.provider_name,
        "accepted": accepted,
        "meta": {
            "page": page_number,
            "confidence": result.confidence,
            "source": result.provider_name,
            "scope": "image",
            "area_ratio": round(area_ratio, 4),
            "accepted": accepted,
        },
    }


def _compute_confidence(
    *,
    page_count: int,
    image_only_pages: int,
    sparse_pages: int,
    element_count: int,
) -> float:
    if page_count == 0 or element_count == 0:
        return 0.0
    penalty = (image_only_pages * 0.25 + sparse_pages * 0.1) / page_count
    return max(0.1, min(1.0, 1.0 - penalty))
