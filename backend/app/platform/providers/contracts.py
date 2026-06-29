"""Provider layer contracts — isolation rules and error taxonomy.

Concrete interfaces and vendor implementations are added alongside the first
real consumer (see ``implementations/``). SDK types must not leave this package.
"""

from __future__ import annotations

from enum import StrEnum


class ProviderCapability(StrEnum):
    """Infrastructure categories APE integrates with (reference for future providers)."""

    LLM = "llm"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    VECTOR_STORE = "vector_store"
    OBJECT_STORAGE = "object_storage"
    OCR = "ocr"
    DOCUMENT_PARSER = "document_parser"
    SOURCE_CONNECTOR = "source_connector"
