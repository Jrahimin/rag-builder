"""Microsoft Word (.docx) text extraction via python-docx."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from docx import Document as open_docx_document
from docx.opc.exceptions import PackageNotFoundError

from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
)
from app.platform.providers.errors import ProviderError

_PARSER_NAME = "python_docx"
_PARSER_VERSION = "1.1.2"
_DOCX_EXTENSIONS = {".docx"}
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocxParserProvider(BaseDocumentParserProvider):
    """Extract paragraph and table text from Office Open XML Word documents."""

    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
    ) -> ParsedDocument:
        del filename, content_type
        if not data:
            return ParsedDocument(
                text="",
                page_count=0,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
            )

        try:
            document = open_docx_document(BytesIO(data))
            parts = _extract_text_parts(document)
            text = "\n\n".join(parts)
        except PackageNotFoundError as exc:
            msg = "Input is not a valid DOCX document."
            raise ProviderError(msg, provider_name=_PARSER_NAME) from exc
        except Exception as exc:
            msg = "Failed to parse DOCX document."
            raise ProviderError(msg, provider_name=_PARSER_NAME) from exc

        return ParsedDocument(
            text=text,
            page_count=1 if text.strip() else 0,
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
        )

    @staticmethod
    def supports(filename: str, content_type: str | None) -> bool:
        lower = filename.lower()
        if any(lower.endswith(ext) for ext in _DOCX_EXTENSIONS):
            return True
        return content_type == _DOCX_CONTENT_TYPE


def _extract_text_parts(document: Any) -> list[str]:
    """Collect non-empty paragraph and table row text in document order."""
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return parts
