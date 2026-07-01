"""Plain text and Markdown document parser."""

from __future__ import annotations

from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
)

_PARSER_NAME = "plain_text"
_PARSER_VERSION = "1.0.0"
_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}


class PlainTextParserProvider(BaseDocumentParserProvider):
    """Decode UTF-8 (with replacement) for text and markdown files."""

    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
    ) -> ParsedDocument:
        del content_type
        if not data:
            return ParsedDocument(
                text="",
                page_count=0,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
            )

        try:
            text = data.decode("utf-8")
            warnings: tuple[str, ...] = ()
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            warnings = ("Non-UTF-8 bytes were replaced during decoding.",)

        return ParsedDocument(
            text=text,
            page_count=1 if text.strip() else 0,
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
            warnings=warnings,
        )

    @staticmethod
    def supports(filename: str, content_type: str | None) -> bool:
        lower = filename.lower()
        if any(lower.endswith(ext) for ext in _TEXT_EXTENSIONS):
            return True
        return content_type in {"text/plain", "text/markdown"}
