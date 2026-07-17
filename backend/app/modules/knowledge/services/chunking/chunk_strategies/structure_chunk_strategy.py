"""Structure-first chunking strategy."""

from __future__ import annotations

from app.modules.knowledge.services.chunking.chunk_strategies.base_chunk_strategy import (
    BaseChunkStrategy,
)
from app.modules.knowledge.services.chunking.chunk_strategies.structure_helpers import (
    chunk_by_sections,
)
from app.modules.knowledge.services.chunking.models import ChunkingContext, DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService

from .recursive_fallback_chunk_strategy import RecursiveFallbackChunkStrategy


class StructureChunkStrategy(BaseChunkStrategy):
    """Chunk using sections, headings, paragraphs, lists, and tables."""

    def __init__(
        self,
        *,
        token_counter: TokenCountingService | None = None,
        fallback: RecursiveFallbackChunkStrategy | None = None,
    ) -> None:
        self._token_counter = token_counter or TokenCountingService()
        self._fallback = fallback or RecursiveFallbackChunkStrategy(
            token_counter=self._token_counter
        )

    def chunk(self, context: ChunkingContext) -> list[DraftChunk]:
        if not context.parsed.elements:
            return self._fallback.split_text(
                context.parsed.text,
                config=context.config,
                base_metadata={"strategy_used": "structure"},
            )
        return chunk_by_sections(
            context,
            token_counter=self._token_counter,
            fallback=self._fallback,
            strategy_name="structure",
        )
