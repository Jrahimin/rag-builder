"""Markdown-aware chunking strategy."""

from __future__ import annotations

from app.modules.knowledge.services.chunking.chunk_strategies.base_chunk_strategy import BaseChunkStrategy
from app.modules.knowledge.services.chunking.chunk_strategies.structure_chunk_strategy import StructureChunkStrategy
from app.modules.knowledge.services.chunking.models import ChunkingContext, DraftChunk


class MarkdownChunkStrategy(BaseChunkStrategy):
    """Chunk markdown documents using section boundaries first."""

    def __init__(self, *, structure_strategy: StructureChunkStrategy | None = None) -> None:
        self._structure = structure_strategy or StructureChunkStrategy()

    def chunk(self, context: ChunkingContext) -> list[DraftChunk]:
        chunks = self._structure.chunk(context)
        for chunk in chunks:
            chunk.metadata["strategy_used"] = "markdown"
        return chunks
