"""Shared page-level extraction models for PDF parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.platform.providers.contracts.document_parser import ParsedElement


@dataclass(frozen=True, slots=True)
class PdfPageExtraction:
    """Native text extraction for a single PDF page."""

    page_number: int
    text: str
    elements: tuple[ParsedElement, ...] = field(default_factory=tuple)
    layout_complex: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
