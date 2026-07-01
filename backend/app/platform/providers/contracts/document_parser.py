"""Document parser provider contract and parsed-text DTO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Normalized output from any document parser implementation."""

    text: str
    page_count: int
    parser_name: str
    parser_version: str
    language: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class BaseDocumentParserProvider(ABC):
    """Extract plain text from raw document bytes."""

    @abstractmethod
    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
    ) -> ParsedDocument:
        """Parse bytes into normalized text and metadata."""
