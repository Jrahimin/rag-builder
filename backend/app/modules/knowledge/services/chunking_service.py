"""Structure-aware chunking orchestration facade."""

from __future__ import annotations

import time
from typing import Any

from app.core.config import ChunkingConfig, ChunkingStrategy, Settings
from app.modules.knowledge.services.chunking.chunk_strategies import (
    RecursiveFallbackChunkStrategy,
)
from app.modules.knowledge.services.chunking.chunk_strategies.heading_chunk_strategy import (
    HeadingChunkStrategy,
)
from app.modules.knowledge.services.chunking.chunk_strategies.markdown_chunk_strategy import (
    MarkdownChunkStrategy,
)
from app.modules.knowledge.services.chunking.chunk_strategies.semantic_chunk_strategy import (
    SemanticChunkStrategy,
)
from app.modules.knowledge.services.chunking.chunk_strategies.structure_chunk_strategy import (
    StructureChunkStrategy,
)
from app.modules.knowledge.services.chunking.chunk_strategy_selector_service import (
    ChunkStrategySelectorService,
)
from app.modules.knowledge.services.chunking.chunk_validation_service import ChunkValidationService
from app.modules.knowledge.services.chunking.models import (
    ChunkingContext,
    ChunkingRunMetadata,
    DraftChunk,
    StructureAnalysis,
    TextChunk,
)
from app.modules.knowledge.services.chunking.sentence_similarity_service import (
    BaseSentenceSimilarityService,
)
from app.modules.knowledge.services.chunking.structure_analyzer_service import (
    StructureAnalyzerService,
)
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.platform.providers.contracts.document_parser import ParsedDocument


class ChunkingService:
    """Orchestrate analyze -> select -> chunk -> validate for parsed documents."""

    def __init__(
        self,
        *,
        config: ChunkingConfig,
        token_counter: TokenCountingService | None = None,
        analyzer: StructureAnalyzerService | None = None,
        selector: ChunkStrategySelectorService | None = None,
        validator: ChunkValidationService | None = None,
        similarity_service: BaseSentenceSimilarityService | None = None,
    ) -> None:
        self._config = config
        self._token_counter = token_counter or TokenCountingService()
        self._analyzer = analyzer or StructureAnalyzerService(
            token_counter=self._token_counter,
            config=config,
        )
        self._selector = selector or ChunkStrategySelectorService()
        self._validator = validator or ChunkValidationService(token_counter=self._token_counter)
        self._similarity_service = similarity_service
        self._fallback = RecursiveFallbackChunkStrategy(token_counter=self._token_counter)
        self._strategies = {
            ChunkingStrategy.MARKDOWN: MarkdownChunkStrategy(),
            ChunkingStrategy.HEADING: HeadingChunkStrategy(
                token_counter=self._token_counter,
                fallback=self._fallback,
            ),
            ChunkingStrategy.STRUCTURE: StructureChunkStrategy(
                token_counter=self._token_counter,
                fallback=self._fallback,
            ),
            ChunkingStrategy.RECURSIVE_FALLBACK: None,
            ChunkingStrategy.RECURSIVE_CHARACTER: None,
        }

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        similarity_service: BaseSentenceSimilarityService | None = None,
    ) -> ChunkingService:
        return cls(config=settings.chunking, similarity_service=similarity_service)

    async def split_document(
        self, parsed: ParsedDocument
    ) -> tuple[list[TextChunk], ChunkingRunMetadata]:
        started = time.perf_counter()
        analysis = self._analyzer.analyze(parsed)
        strategy = self._selector.select(parsed, analysis, self._config)
        context = ChunkingContext(
            parsed=parsed, config=self._config, analysis=analysis, strategy=strategy
        )

        semantic_used = False
        boundary_provider: str | None = None
        boundary_model: str | None = None
        boundary_provider_version: str | None = None

        if strategy is ChunkingStrategy.SEMANTIC:
            if self._similarity_service is None:
                msg = "Semantic chunking requires a sentence similarity service."
                raise ValueError(msg)
            semantic_strategy = SemanticChunkStrategy(similarity_service=self._similarity_service)
            drafts = await semantic_strategy.chunk_async(context)
            semantic_used = True
            if semantic_strategy.last_boundary_result is not None:
                boundary_provider = semantic_strategy.last_boundary_result.provider
                boundary_model = semantic_strategy.last_boundary_result.model
                boundary_provider_version = semantic_strategy.last_boundary_result.provider_version
        elif strategy in {
            ChunkingStrategy.RECURSIVE_FALLBACK,
            ChunkingStrategy.RECURSIVE_CHARACTER,
        }:
            drafts = self._fallback.split_text(
                parsed.text,
                config=self._config,
                base_metadata={"strategy_used": strategy.value},
            )
        else:
            strategy_impl = self._strategies.get(strategy)
            if strategy_impl is None:
                msg = f"Unsupported chunking strategy: {strategy!r}"
                raise ValueError(msg)
            drafts = strategy_impl.chunk(context)

        validated = self._validator.validate(drafts, self._config)
        chunks = self._to_text_chunks(validated, parsed, analysis, strategy)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        avg_tokens = sum(chunk.token_count for chunk in chunks) / len(chunks) if chunks else 0.0
        run_metadata = ChunkingRunMetadata(
            strategy_used=strategy,
            structure_score=analysis.structure_score,
            structure_signals=analysis.signals.to_dict(),
            semantic_refinement_used=semantic_used,
            similarity_drop_threshold=self._config.similarity_drop_threshold
            if semantic_used
            else None,
            boundary_provider=boundary_provider,
            boundary_model=boundary_model,
            boundary_provider_version=boundary_provider_version,
            chunker_version=self._config.chunker_version,
            token_count_method=self._config.token_count_method,
            processing_time_ms=elapsed_ms,
            chunk_count=len(chunks),
            avg_token_count=round(avg_tokens, 2),
        )
        return chunks, run_metadata

    def split(self, text: str, *, page_count: int | None = None) -> list[TextChunk]:
        """Backward-compatible sync split for plain text using recursive fallback."""
        del page_count
        parsed = ParsedDocument(
            text=text,
            page_count=1 if text.strip() else 0,
            parser_name="legacy",
            parser_version="1.0.0",
        )
        drafts = self._fallback.split_text(
            text,
            config=self._config,
            base_metadata={"strategy_used": "recursive_fallback"},
        )
        validated = self._validator.validate(drafts, self._config)
        analysis = self._analyzer.analyze(parsed)
        return self._to_text_chunks(
            validated,
            parsed,
            analysis,
            ChunkingStrategy.RECURSIVE_FALLBACK,
        )

    def _to_text_chunks(
        self,
        drafts: list[DraftChunk],
        parsed: ParsedDocument,
        analysis: StructureAnalysis,
        strategy: ChunkingStrategy,
    ) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for index, draft in enumerate(drafts):
            metadata = self._build_chunk_metadata(draft, parsed, analysis, strategy)
            chunks.append(
                TextChunk(
                    content=draft.content,
                    chunk_index=index,
                    char_start=draft.char_start,
                    char_end=draft.char_end,
                    page_number=draft.page_start,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    token_count=self._token_counter.count(draft.content),
                    chunk_metadata=metadata,
                )
            )
        return chunks

    def _build_chunk_metadata(
        self,
        draft: DraftChunk,
        parsed: ParsedDocument,
        analysis: StructureAnalysis,
        strategy: ChunkingStrategy,
    ) -> dict[str, Any]:
        metadata = dict(draft.metadata)
        metadata.update(
            {
                "chunk_order": draft.chunk_order,
                "section_title": draft.section_title,
                "heading_level": draft.heading_level,
                "parser_version": parsed.parser_version,
                "parsed_document_version": parsed.parsed_document_version,
                "chunker_version": self._config.chunker_version,
                "strategy_used": strategy.value,
                "structure_score": analysis.structure_score,
                "structure_signals": analysis.signals.to_dict(),
                "token_count_method": self._config.token_count_method,
                "language": parsed.language,
            }
        )
        if parsed.ocr_quality is not None and "ocr_confidence" not in metadata:
            metadata["ocr_confidence"] = parsed.ocr_quality
        if parsed.parse_quality_score is not None and "parse_quality_score" not in metadata:
            metadata["parse_quality_score"] = parsed.parse_quality_score
        extraction_method = parsed.structure_hints.get("extraction_method")
        if extraction_method is not None and "extraction_method" not in metadata:
            metadata["extraction_method"] = extraction_method
        partial_extraction = parsed.structure_hints.get("partial_extraction")
        if partial_extraction is not None and "partial_extraction" not in metadata:
            metadata["partial_extraction"] = partial_extraction
        return metadata
