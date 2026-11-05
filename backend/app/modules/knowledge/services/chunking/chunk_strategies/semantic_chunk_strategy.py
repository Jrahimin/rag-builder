"""Semantic chunking strategy using sentence similarity boundaries."""

from __future__ import annotations

from itertools import pairwise

from app.modules.knowledge.services.chunking.chunk_strategies.base_chunk_strategy import (
    BaseChunkStrategy,
)
from app.modules.knowledge.services.chunking.models import ChunkingContext, DraftChunk
from app.modules.knowledge.services.chunking.sentence_similarity_service import (
    BaseSentenceSimilarityService,
    BoundaryDetectionResult,
    split_sentences,
)
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService

from .recursive_fallback_chunk_strategy import RecursiveFallbackChunkStrategy


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
        self._fallback = fallback or RecursiveFallbackChunkStrategy(
            token_counter=self._token_counter
        )
        self._last_boundary_result: BoundaryDetectionResult | None = None

    @property
    def last_boundary_result(self) -> BoundaryDetectionResult | None:
        return self._last_boundary_result

    async def chunk_async(self, context: ChunkingContext) -> list[DraftChunk]:
        text = context.parsed.text
        if not text.strip():
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
        sentence_spans = []
        cursor = 0
        for sentence in sentences:
            position = text.find(sentence, cursor)
            if position < 0:
                return self._fallback.split_text(text, config=context.config)
            sentence_spans.append((position, position + len(sentence)))
            cursor = position + len(sentence)
        for start_index, end_index in pairwise(ordered_boundaries):
            start, end = sentence_spans[start_index][0], sentence_spans[end_index - 1][1]
            segment = text[start:end]
            provenance = [
                {
                    "element_id": f"semantic:{start}:{end}",
                    "role": "text",
                    "text": segment,
                    "char_start": start,
                    "char_end": end,
                    "provenance": "exact_source_span",
                }
            ]
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
                            "source_spans": provenance,
                        },
                    )
                )
            else:
                chunks.append(
                    DraftChunk(
                        content=segment,
                        char_start=start,
                        char_end=end,
                        metadata={
                            "strategy_used": "semantic",
                            "semantic_refinement_used": True,
                            "source_spans": provenance,
                        },
                    )
                )
        return chunks

    def chunk(self, context: ChunkingContext) -> list[DraftChunk]:
        msg = "SemanticChunkStrategy requires async chunking via chunk_async()."
        raise RuntimeError(msg)
