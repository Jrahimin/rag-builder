"""Rule-based document structure analysis."""

from __future__ import annotations

from app.core.config import ChunkingConfig
from app.modules.knowledge.services.chunking.models import StructureAnalysis, StructureSignals
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.platform.providers.contracts.document_parser import (
    ParsedDocument,
    ParsedElementType,
    SourceFormat,
)


class StructureAnalyzerService:
    """Evaluate document structure quality using deterministic signals."""

    def __init__(
        self,
        *,
        token_counter: TokenCountingService | None = None,
        config: ChunkingConfig | None = None,
    ) -> None:
        self._token_counter = token_counter or TokenCountingService()
        self._config = config

    def analyze(self, parsed: ParsedDocument) -> StructureAnalysis:
        config = self._config
        long_block_threshold = config.long_block_token_threshold if config else 600

        headings = [element for element in parsed.elements if element.element_type == ParsedElementType.HEADING]
        tables = [element for element in parsed.elements if element.element_type == ParsedElementType.TABLE]
        lists = [element for element in parsed.elements if element.element_type == ParsedElementType.LIST]
        code_blocks = [element for element in parsed.elements if element.element_type == ParsedElementType.CODE_BLOCK]
        paragraphs = [
            element
            for element in parsed.elements
            if element.element_type in {ParsedElementType.PARAGRAPH, ParsedElementType.UNKNOWN}
        ]

        paragraph_tokens = [self._token_counter.count(element.text) for element in paragraphs]
        long_blocks = sum(1 for count in paragraph_tokens if count >= long_block_threshold)
        avg_paragraph_tokens = (
            sum(paragraph_tokens) / len(paragraph_tokens) if paragraph_tokens else 0.0
        )

        signals = StructureSignals(
            has_headings=bool(headings),
            heading_count=len(headings),
            max_heading_level=max((element.heading_level or 0) for element in headings) if headings else 0,
            has_tables=bool(tables),
            table_count=len(tables),
            has_lists=bool(lists),
            list_count=len(lists),
            has_code_blocks=bool(code_blocks),
            paragraph_count=len(paragraphs),
            avg_paragraph_tokens=round(avg_paragraph_tokens, 2),
            long_block_count=long_blocks,
            markdown_detected=parsed.source_format is SourceFormat.MARKDOWN,
            html_detected=parsed.source_format is SourceFormat.HTML,
            parser_confidence=parsed.parser_confidence,
            ocr_quality=parsed.ocr_quality,
            source_format=parsed.source_format.value,
        )
        score = self._compute_score(signals)
        return StructureAnalysis(structure_score=score, signals=signals)

    def _compute_score(self, signals: StructureSignals) -> float:
        score = 0.0
        if signals.has_headings:
            score += 0.25
        if signals.heading_count >= 2:
            score += 0.1
        if signals.has_tables:
            score += 0.1
        if signals.has_lists:
            score += 0.05
        if signals.has_code_blocks:
            score += 0.05
        if signals.paragraph_count >= 3:
            score += 0.15
        if signals.markdown_detected:
            score += 0.1
        if signals.html_detected:
            score += 0.05
        score += min(0.2, signals.parser_confidence * 0.2)
        if signals.long_block_count:
            score -= min(0.2, signals.long_block_count * 0.05)
        if signals.ocr_quality is not None and signals.ocr_quality < 0.5:
            score -= 0.2
        return max(0.0, min(1.0, round(score, 3)))
