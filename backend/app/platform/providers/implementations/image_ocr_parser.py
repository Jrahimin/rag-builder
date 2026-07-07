"""Image OCR document parser."""

from __future__ import annotations

from app.core.config import get_settings
from app.platform.domain.language_detection import detect_language
from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
    ParsedElement,
    ParsedElementType,
    SourceFormat,
)
from app.platform.providers.contracts.ocr import OcrImageInput
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.ocr_factory import get_ocr_provider
from app.platform.providers.implementations.parsed_element_builder import finalize_elements
from app.platform.providers.implementations.pdf_page_layout import accept_ocr_result

_PARSER_NAME = "image_ocr"
_PARSER_VERSION = "1.0.0"
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
    "image/bmp",
    "image/webp",
}


class ImageOcrParserProvider(BaseDocumentParserProvider):
    """Parse raster images via the configured OCR provider."""

    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        ocr_lang: str | None = None,
    ) -> ParsedDocument:
        del filename
        settings = get_settings()
        if not settings.ocr.enabled:
            msg = "OCR is disabled. Enable APE_OCR__ENABLED to parse image uploads."
            raise ProviderError(msg, provider_name=_PARSER_NAME)

        ocr = get_ocr_provider(lang=ocr_lang)
        ocr_cfg = settings.ocr
        result = ocr.recognize(
            OcrImageInput(data=data, mime_type=content_type, page_number=1),
        )
        accepted = accept_ocr_result(
            result.text,
            result.confidence,
            min_text_chars=ocr_cfg.min_text_chars,
            min_confidence=ocr_cfg.min_page_confidence,
        )
        if not result.text.strip() or not accepted:
            return ParsedDocument(
                text="",
                page_count=0,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                source_format=SourceFormat.UNKNOWN,
                parser_confidence=result.confidence,
                ocr_quality=result.confidence,
                warnings=(
                    "OCR produced no text."
                    if not result.text.strip()
                    else "OCR below min_text_chars or min_page_confidence.",
                ),
                structure_hints={
                    "ocr_pages": [
                        {
                            "page": 1,
                            "confidence": result.confidence,
                            "source": result.provider_name,
                        }
                    ],
                    "ocr_page_count": 1,
                    "min_page_confidence": result.confidence,
                },
            )

        element = ParsedElement(
            text=result.text,
            element_type=ParsedElementType.PARAGRAPH,
            page_start=1,
            page_end=1,
            metadata={
                "ocr_confidence": result.confidence,
                "ocr_source": result.provider_name,
            },
        )
        projection, finalized = finalize_elements([element])
        language_result = detect_language(projection)
        return ParsedDocument(
            text=projection,
            page_count=1,
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
            elements=finalized,
            source_format=SourceFormat.UNKNOWN,
            parser_confidence=result.confidence,
            ocr_quality=result.confidence,
            language=language_result.primary_language,
            structure_hints={
                "ocr_pages": [
                    {
                        "page": 1,
                        "confidence": result.confidence,
                        "source": result.provider_name,
                    }
                ],
                "ocr_page_count": 1,
                "min_page_confidence": result.confidence,
                "language_confidence": language_result.confidence,
                "languages": language_result.languages,
                "is_mixed": language_result.is_mixed,
            },
        )

    @staticmethod
    def supports(filename: str, content_type: str | None) -> bool:
        lower = filename.lower()
        if any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS):
            return True
        return content_type in _IMAGE_CONTENT_TYPES
