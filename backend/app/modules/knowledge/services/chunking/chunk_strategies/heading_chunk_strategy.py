"""Heading-based chunking strategy for DOCX and similar documents."""

from __future__ import annotations

from app.modules.knowledge.services.chunking.chunk_strategies.base_chunk_strategy import BaseChunkStrategy
from app.modules.knowledge.services.chunking.chunk_strategies.recursive_fallback_chunk_strategy import (
    RecursiveFallbackChunkStrategy,
)
from app.modules.knowledge.services.chunking.chunk_strategies.structure_helpers import group_sections
from app.modules.knowledge.services.chunking.models import ChunkingContext, DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.platform.providers.contracts.document_parser import ParsedElementType


class HeadingChunkStrategy(BaseChunkStrategy):
    """Chunk documents primarily by heading sections."""

    def __init__(
        self,
        *,
        token_counter: TokenCountingService | None = None,
        fallback: RecursiveFallbackChunkStrategy | None = None,
    ) -> None:
        self._token_counter = token_counter or TokenCountingService()
        self._fallback = fallback or RecursiveFallbackChunkStrategy(token_counter=self._token_counter)

    def chunk(self, context: ChunkingContext) -> list[DraftChunk]:
        elements = list(context.parsed.elements)
        if not elements:
            return self._fallback.split_text(
                context.parsed.text,
                config=context.config,
                base_metadata={"strategy_used": "heading"},
            )

        chunks: list[DraftChunk] = []
        for section in group_sections(elements):
            section_title = next(
                (element.text.strip() for element in section if element.element_type is ParsedElementType.HEADING),
                None,
            )
            content = "\n\n".join(element.text.strip() for element in section if element.text.strip())
            if not content:
                continue
            first = section[0]
            last = section[-1]
            if self._token_counter.count(content) > context.config.max_tokens:
                chunks.extend(
                    self._fallback.split_text(
                        content,
                        config=context.config,
                        base_metadata={"strategy_used": "heading", "section_chunk": True},
                        page_start=first.page_start,
                        page_end=last.page_end,
                        section_title=section_title,
                        heading_level=first.heading_level,
                    )
                )
            else:
                chunks.append(
                    DraftChunk(
                        content=content,
                        char_start=first.char_start,
                        char_end=last.char_end,
                        page_start=first.page_start,
                        page_end=last.page_end,
                        section_title=section_title,
                        heading_level=first.heading_level,
                        metadata={"strategy_used": "heading", "section_chunk": True},
                    )
                )
        return chunks
