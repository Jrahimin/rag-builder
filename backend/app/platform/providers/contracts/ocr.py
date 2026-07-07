"""OCR provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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
