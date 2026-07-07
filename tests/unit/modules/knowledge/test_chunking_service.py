"""Unit tests for ChunkingService and chunking pipeline."""

from __future__ import annotations

import pytest

from app.core.config import ChunkingConfig, ChunkingStrategy
from app.modules.knowledge.services.chunking.chunk_strategy_selector_service import ChunkStrategySelectorService
from app.modules.knowledge.services.chunking.chunk_validation_service import ChunkValidationService
from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking.sentence_similarity_service import HashSentenceSimilarityService
from app.modules.knowledge.services.chunking.structure_analyzer_service import StructureAnalyzerService
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.platform.providers.contracts.document_parser import (
    PARSED_DOCUMENT_VERSION,
    ParsedDocument,
    ParsedElement,
    ParsedElementType,
    SourceFormat,
)

pytestmark = pytest.mark.unit


def _markdown_parsed() -> ParsedDocument:
    elements = (
        ParsedElement(
            text="Introduction",
            element_type=ParsedElementType.HEADING,
            heading_level=1,
            page_start=1,
            page_end=1,
            char_start=0,
            char_end=12,
        ),
        ParsedElement(
            text="First paragraph about chunking.",
            element_type=ParsedElementType.PARAGRAPH,
            page_start=1,
            page_end=1,
            char_start=14,
            char_end=44,
        ),
        ParsedElement(
            text="Second paragraph with more detail.",
            element_type=ParsedElementType.PARAGRAPH,
            page_start=1,
            page_end=1,
            char_start=46,
            char_end=80,
        ),
    )
    return ParsedDocument(
        text="Introduction\n\nFirst paragraph about chunking.\n\nSecond paragraph with more detail.",
        page_count=1,
        parser_name="plain_text",
        parser_version="2.0.0",
        parsed_document_version=PARSED_DOCUMENT_VERSION,
        elements=elements,
        source_format=SourceFormat.MARKDOWN,
        parser_confidence=0.9,
    )


@pytest.mark.asyncio
async def test_split_document_uses_markdown_strategy_for_markdown() -> None:
    service = ChunkingService.from_settings(
        _settings_with_strategy(ChunkingStrategy.AUTO),
        similarity_service=HashSentenceSimilarityService(),
    )
    chunks, run_metadata = await service.split_document(_markdown_parsed())

    assert chunks
    assert run_metadata.strategy_used is ChunkingStrategy.MARKDOWN
    assert chunks[0].chunk_metadata["strategy_used"] == "markdown"
    assert chunks[0].chunk_metadata["parsed_document_version"] == PARSED_DOCUMENT_VERSION
    assert "structure_signals" in chunks[0].chunk_metadata


def test_split_legacy_plain_text_still_returns_chunks() -> None:
    text = " ".join(f"token{i}" for i in range(600))
    service = ChunkingService(config=ChunkingConfig(target_tokens=100, max_tokens=100, overlap_tokens=10))
    chunks = service.split(text.strip(), page_count=1)

    assert len(chunks) > 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].token_count > 0


def test_structure_analyzer_exposes_signals() -> None:
    analysis = StructureAnalyzerService().analyze(_markdown_parsed())
    assert analysis.structure_score > 0
    assert analysis.signals.has_headings is True
    assert analysis.signals.markdown_detected is True


def test_selector_chooses_semantic_for_plain_text() -> None:
    parsed = ParsedDocument(
        text="A long unstructured blob without headings.",
        page_count=1,
        parser_name="plain_text",
        parser_version="2.0.0",
        elements=(
            ParsedElement(
                text="A long unstructured blob without headings.",
                element_type=ParsedElementType.PARAGRAPH,
                page_start=1,
                page_end=1,
            ),
        ),
        source_format=SourceFormat.PLAIN_TEXT,
        parser_confidence=0.5,
    )
    analysis = StructureAnalyzerService().analyze(parsed)
    strategy = ChunkStrategySelectorService().select(parsed, analysis, ChunkingConfig())
    assert strategy is ChunkingStrategy.SEMANTIC


def test_validation_preserves_order_when_merging_tiny_chunks() -> None:
    config = ChunkingConfig(min_tokens=20, max_tokens=200)
    validator = ChunkValidationService()
    drafts = [
        DraftChunk(content="tiny", chunk_order=0),
        DraftChunk(content="Another small chunk with a few more words.", chunk_order=1),
        DraftChunk(content="Final chunk with enough content to remain separate.", chunk_order=2),
    ]
    validated = validator.validate(drafts, config)
    contents = [chunk.content for chunk in validated]
    assert contents[0].startswith("tiny")
    assert "Final chunk" in contents[-1]


@pytest.mark.asyncio
async def test_semantic_chunking_uses_similarity_service_not_embedder_directly() -> None:
    service = ChunkingService(
        config=ChunkingConfig(strategy=ChunkingStrategy.SEMANTIC, target_tokens=50, max_tokens=150),
        similarity_service=HashSentenceSimilarityService(),
    )
    parsed = ParsedDocument(
        text="Cats are mammals. Dogs are mammals. Quantum physics is complex.",
        page_count=1,
        parser_name="plain_text",
        parser_version="2.0.0",
        source_format=SourceFormat.PLAIN_TEXT,
    )
    chunks, run_metadata = await service.split_document(parsed)
    assert chunks
    assert run_metadata.semantic_refinement_used is True
    assert run_metadata.boundary_provider == "hash"


def _settings_with_strategy(strategy: ChunkingStrategy):
    from app.core.config import Settings

    return Settings(chunking=ChunkingConfig(strategy=strategy))
