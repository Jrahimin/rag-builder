"""Provider layer contracts — capability reference and concrete interfaces."""

from __future__ import annotations

from enum import StrEnum

from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
)
from app.platform.providers.contracts.storage import BaseStorageProvider

__all__ = [
    "BaseDocumentParserProvider",
    "BaseStorageProvider",
    "ParsedDocument",
    "ProviderCapability",
]


class ProviderCapability(StrEnum):
    """Infrastructure categories APE integrates with (reference for future providers)."""

    LLM = "llm"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    OBJECT_STORAGE = "object_storage"
    OCR = "ocr"
    DOCUMENT_PARSER = "document_parser"
    SOURCE_CONNECTOR = "source_connector"
