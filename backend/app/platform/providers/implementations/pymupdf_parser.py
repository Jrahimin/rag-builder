"""PDF text extraction via PyMuPDF (fitz)."""

from __future__ import annotations

import fitz

from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
)
from app.platform.providers.errors import ProviderError

_PARSER_NAME = "pymupdf"
_PARSER_VERSION = "1.26.3"


class PyMuPDFParserProvider(BaseDocumentParserProvider):
    """Extract text from digital PDFs using PyMuPDF."""

    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
    ) -> ParsedDocument:
        del filename
        if content_type not in {None, "application/pdf"} and not data.startswith(b"%PDF"):
            msg = "Input is not a valid PDF document."
            raise ProviderError(msg, provider_name=_PARSER_NAME)

        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                pages: list[str] = []
                warnings: list[str] = []
                for page in document:
                    page_text = page.get_text().strip()
                    if page_text:
                        pages.append(page_text)
                    elif page.get_images():
                        warnings.append(
                            f"Page {page.number + 1} contains images only; OCR is not enabled."
                        )
                text = "\n\n".join(pages)
                page_count = document.page_count
        except Exception as exc:
            msg = "Failed to parse PDF document."
            raise ProviderError(msg, provider_name=_PARSER_NAME) from exc

        return ParsedDocument(
            text=text,
            page_count=page_count,
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
            warnings=tuple(warnings),
        )

    @staticmethod
    def supports(filename: str, content_type: str | None) -> bool:
        lower = filename.lower()
        if lower.endswith(".pdf"):
            return True
        return content_type == "application/pdf"
