"""OCR provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class OcrBoundingBox:
    """Provider-neutral normalized page coordinates in the range 0..1."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(frozen=True, slots=True)
class OcrWord:
    """One OCR word with optional geometry and confidence."""

    text: str
    bounding_box: OcrBoundingBox | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class OcrParagraph:
    """A provider paragraph containing words in reading order."""

    text: str
    words: tuple[OcrWord, ...] = field(default_factory=tuple)
    bounding_box: OcrBoundingBox | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class OcrBlock:
    """A provider block containing paragraphs in reading order."""

    text: str
    paragraphs: tuple[OcrParagraph, ...] = field(default_factory=tuple)
    bounding_box: OcrBoundingBox | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class OcrImageInput:
    """Image bytes submitted for OCR."""

    data: bytes
    mime_type: str | None = None
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    """OCR output for a single page or image."""

    text: str
    confidence: float
    provider_name: str
    lines: tuple[str, ...] = field(default_factory=tuple)
    blocks: tuple[OcrBlock, ...] = field(default_factory=tuple)
    page_number: int | None = None


class OCRProvider(ABC):
    """Extract text from images or scanned pages."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier."""

    @abstractmethod
    def recognize(self, image: OcrImageInput) -> OcrPageResult:
        """Run OCR on a single image."""
