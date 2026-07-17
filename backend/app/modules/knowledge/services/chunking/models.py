"""Shared chunking domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import ChunkingConfig, ChunkingStrategy
from app.platform.providers.contracts.document_parser import ParsedDocument


@dataclass(frozen=True, slots=True)
class StructureSignals:
    """Explainable structure signals used for strategy selection."""

    has_headings: bool = False
    heading_count: int = 0
    max_heading_level: int = 0
    has_tables: bool = False
    table_count: int = 0
    has_lists: bool = False
    list_count: int = 0
    has_code_blocks: bool = False
    paragraph_count: int = 0
    avg_paragraph_tokens: float = 0.0
    long_block_count: int = 0
    markdown_detected: bool = False
    html_detected: bool = False
    parser_confidence: float = 1.0
    ocr_quality: float | None = None
    source_format: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_headings": self.has_headings,
            "heading_count": self.heading_count,
            "max_heading_level": self.max_heading_level,
            "has_tables": self.has_tables,
            "table_count": self.table_count,
            "has_lists": self.has_lists,
            "list_count": self.list_count,
            "has_code_blocks": self.has_code_blocks,
            "paragraph_count": self.paragraph_count,
            "avg_paragraph_tokens": self.avg_paragraph_tokens,
            "long_block_count": self.long_block_count,
            "markdown_detected": self.markdown_detected,
            "html_detected": self.html_detected,
            "parser_confidence": self.parser_confidence,
            "ocr_quality": self.ocr_quality,
            "source_format": self.source_format,
        }


@dataclass(frozen=True, slots=True)
class StructureAnalysis:
    """Rule-based structure quality assessment."""

    structure_score: float
    signals: StructureSignals


@dataclass(frozen=True, slots=True)
class ChunkingRunMetadata:
    """Processing-run metadata stored once per document chunking run."""

    strategy_used: ChunkingStrategy
    structure_score: float
    structure_signals: dict[str, Any]
    semantic_refinement_used: bool = False
    similarity_drop_threshold: float | None = None
    boundary_provider: str | None = None
    boundary_model: str | None = None
    boundary_provider_version: str | None = None
    chunker_version: str = "2.0.0"
    token_count_method: str = "unicode_property_v1"
    processing_time_ms: int | None = None
    chunk_count: int = 0
    avg_token_count: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_used": self.strategy_used.value,
            "structure_score": self.structure_score,
            "structure_signals": self.structure_signals,
            "semantic_refinement_used": self.semantic_refinement_used,
            "similarity_drop_threshold": self.similarity_drop_threshold,
            "boundary_provider": self.boundary_provider,
            "boundary_model": self.boundary_model,
            "boundary_provider_version": self.boundary_provider_version,
            "chunker_version": self.chunker_version,
            "token_count_method": self.token_count_method,
            "processing_time_ms": self.processing_time_ms,
            "chunk_count": self.chunk_count,
            "avg_token_count": self.avg_token_count,
        }


@dataclass(frozen=True, slots=True)
class TextChunk:
    """A single split segment ready for persistence."""

    content: str
    chunk_index: int
    char_start: int | None
    char_end: int | None
    page_number: int | None
    page_start: int | None
    page_end: int | None
    token_count: int
    chunk_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChunkingContext:
    """Input context for chunk strategies."""

    parsed: ParsedDocument
    config: ChunkingConfig
    analysis: StructureAnalysis
    strategy: ChunkingStrategy


@dataclass
class DraftChunk:
    """Mutable chunk draft used inside strategies before validation."""

    content: str
    char_start: int | None = None
    char_end: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    section_title: str | None = None
    heading_level: int | None = None
    chunk_order: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def token_count(self, counter: Any) -> int:
        return counter.count(self.content)
