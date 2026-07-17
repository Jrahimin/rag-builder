"""Composite document parser — selects implementation by filename and MIME type."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
)
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.docx_parser import DocxParserProvider
from app.platform.providers.implementations.image_ocr_parser import ImageOcrParserProvider
from app.platform.providers.implementations.pdf_extraction_workflow import PdfExtractionWorkflow
from app.platform.providers.implementations.plain_text_parser import PlainTextParserProvider


class CompositeDocumentParserProvider(BaseDocumentParserProvider):
    """Route parsing to the first matching provider."""

    def __init__(
        self,
        *,
        pdf_parser: BaseDocumentParserProvider | None = None,
        docx_parser: BaseDocumentParserProvider | None = None,
        text_parser: BaseDocumentParserProvider | None = None,
        image_parser: BaseDocumentParserProvider | None = None,
    ) -> None:
        self._pdf_parser = pdf_parser or PdfExtractionWorkflow()
        self._docx_parser = docx_parser or DocxParserProvider()
        self._text_parser = text_parser or PlainTextParserProvider()
        self._image_parser = image_parser or ImageOcrParserProvider()

    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        ocr_lang: str | None = None,
    ) -> ParsedDocument:
        if ImageOcrParserProvider.supports(filename, content_type):
            return self._image_parser.parse(
                data=data,
                filename=filename,
                content_type=content_type,
                ocr_lang=ocr_lang,
            )
        if PdfExtractionWorkflow.supports(filename, content_type):
            return self._pdf_parser.parse(
                data=data,
                filename=filename,
                content_type=content_type,
                ocr_lang=ocr_lang,
            )
        if DocxParserProvider.supports(filename, content_type):
            return self._docx_parser.parse(
                data=data,
                filename=filename,
                content_type=content_type,
                ocr_lang=ocr_lang,
            )
        if PlainTextParserProvider.supports(filename, content_type):
            return self._text_parser.parse(
                data=data,
                filename=filename,
                content_type=content_type,
                ocr_lang=ocr_lang,
            )
        msg = f"Unsupported document type for parsing: {filename!r}"
        raise ProviderError(msg, provider_name="document_parser")


@lru_cache
def get_document_parser() -> CompositeDocumentParserProvider:
    """Return the process-scoped document parser."""
    return create_document_parser(get_settings())


def create_document_parser(settings: Settings) -> CompositeDocumentParserProvider:
    """Build parsers from an explicit (possibly job-snapshotted) settings view."""
    return CompositeDocumentParserProvider(
        pdf_parser=PdfExtractionWorkflow(
            parsing_config=settings.parsing,
            ocr_config=settings.ocr,
            settings=settings,
        ),
        image_parser=ImageOcrParserProvider(settings=settings),
    )
