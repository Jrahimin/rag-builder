"""Document parser provider contract and parsed document DTOs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


PARSED_DOCUMENT_VERSION = "1.0.0"


class ParsedElementType(StrEnum):
    """Structured element types emitted by document parsers."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    CODE_BLOCK = "code_block"
    PAGE_BREAK = "page_break"
    UNKNOWN = "unknown"


class SourceFormat(StrEnum):
    """Detected source format for a parsed document."""

    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"
    PLAIN_TEXT = "plain_text"
    HTML = "html"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParsedElement:
    """A single structured element within a parsed document."""

    text: str
    element_type: ParsedElementType
    page_start: int | None = None
    page_end: int | None = None
    heading_level: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Normalized structured output from any document parser implementation."""

    text: str
    page_count: int
    parser_name: str
    parser_version: str
    parsed_document_version: str = PARSED_DOCUMENT_VERSION
    elements: tuple[ParsedElement, ...] = field(default_factory=tuple)
    source_format: SourceFormat = SourceFormat.UNKNOWN
    parser_confidence: float = 1.0
    parse_quality_score: float | None = None
    ocr_quality: float | None = None
    language: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    structure_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the parsed document for JSON sidecar storage."""
        return {
            "parsed_document_version": self.parsed_document_version,
            "text": self.text,
            "page_count": self.page_count,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "source_format": self.source_format.value,
            "parser_confidence": self.parser_confidence,
            "parse_quality_score": self.parse_quality_score,
            "ocr_quality": self.ocr_quality,
            "language": self.language,
            "warnings": list(self.warnings),
            "structure_hints": self.structure_hints,
            "elements": [
                {
                    "text": element.text,
                    "element_type": element.element_type.value,
                    "page_start": element.page_start,
                    "page_end": element.page_end,
                    "heading_level": element.heading_level,
                    "char_start": element.char_start,
                    "char_end": element.char_end,
                    "metadata": element.metadata,
                }
                for element in self.elements
            ],
        }


def build_plain_text_projection(elements: list[ParsedElement]) -> str:
    """Join element text into a normalized plain-text projection."""
    parts = [element.text.strip() for element in elements if element.text.strip()]
    return "\n\n".join(parts)


class BaseDocumentParserProvider(ABC):
    """Extract structured text from raw document bytes."""

    @abstractmethod
    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        ocr_lang: str | None = None,
    ) -> ParsedDocument:
        """Parse bytes into normalized text, elements, and metadata."""
