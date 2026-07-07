"""Semantic chunking strategy using sentence similarity boundaries."""

from __future__ import annotations

from app.modules.knowledge.services.chunking.chunk_strategies.base_chunk_strategy import BaseChunkStrategy
from app.modules.knowledge.services.chunking.chunk_strategies.recursive_fallback_chunk_strategy import (
    RecursiveFallbackChunkStrategy,
)
from app.modules.knowledge.services.chunking.models import ChunkingContext, DraftChunk
from app.modules.knowledge.services.chunking.sentence_similarity_service import (
    BaseSentenceSimilarityService,
    BoundaryDetectionResult,
    split_sentences,
)
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService


class SemanticChunkStrategy(BaseChunkStrategy):
    """Chunk weakly structured documents using semantic boundary detection."""

    def __init__(
        self,
        *,
        similarity_service: BaseSentenceSimilarityService,
        token_counter: TokenCountingService | None = None,
        fallback: RecursiveFallbackChunkStrategy | None = None,
    ) -> None:
        self._similarity_service = similarity_service
        self._token_counter = token_counter or TokenCountingService()
        self._fallback = fallback or RecursiveFallbackChunkStrategy(token_counter=self._token_counter)
        self._last_boundary_result: BoundaryDetectionResult | None = None

    @property
    def last_boundary_result(self) -> BoundaryDetectionResult | None:
        return self._last_boundary_result

    async def chunk_async(self, context: ChunkingContext) -> list[DraftChunk]:
        text = context.parsed.text.strip()
        if not text:
            return []

        sentences = split_sentences(text)
        if len(sentences) <= 1:
            return self._fallback.split_text(
                text,
                config=context.config,
                base_metadata={"strategy_used": "semantic", "semantic_refinement_used": True},
            )

        boundary_result = await self._similarity_service.detect_boundaries(
            sentences,
            drop_threshold=context.config.similarity_drop_threshold,
        )
        self._last_boundary_result = boundary_result

        boundaries = {0, *boundary_result.boundaries, len(sentences)}
        ordered_boundaries = sorted(boundaries)
        chunks: list[DraftChunk] = []
        for start_index, end_index in zip(ordered_boundaries, ordered_boundaries[1:], strict=False):
            segment = " ".join(sentences[start_index:end_index]).strip()
            if not segment:
                continue
            if self._token_counter.count(segment) > context.config.max_tokens:
                chunks.extend(
                    self._fallback.split_text(
                        segment,
                        config=context.config,
                        base_metadata={
                            "strategy_used": "semantic",
                            "semantic_refinement_used": True,
                        },
                    )
                )
            else:
                chunks.append(
                    DraftChunk(
                        content=segment,
                        metadata={
                            "strategy_used": "semantic",
                            "semantic_refinement_used": True,
                        },
                    )
                )
        return chunks

    def chunk(self, context: ChunkingContext) -> list[DraftChunk]:
        msg = "SemanticChunkStrategy requires async chunking via chunk_async()."
        raise RuntimeError(msg)
