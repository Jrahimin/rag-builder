"""Deterministic chunk strategy selection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import ChunkingConfig, ChunkingStrategy
from app.modules.knowledge.services.chunking.models import StructureAnalysis
from app.platform.providers.contracts.document_parser import ParsedDocument, SourceFormat


@dataclass(frozen=True, slots=True)
class SelectorRule:
    """A registered selector rule evaluated in priority order."""

    name: str
    priority: int
    predicate: Callable[[ParsedDocument, StructureAnalysis, ChunkingConfig], bool]
    strategy: ChunkingStrategy


class ChunkStrategySelectorService:
    """Select a chunking strategy from registered rules and config overrides."""

    def __init__(self, *, rules: list[SelectorRule] | None = None) -> None:
        self._rules = sorted(rules or _default_rules(), key=lambda rule: rule.priority)

    def select(
        self,
        parsed: ParsedDocument,
        analysis: StructureAnalysis,
        config: ChunkingConfig,
    ) -> ChunkingStrategy:
        if config.strategy is not ChunkingStrategy.AUTO:
            return config.strategy

        for rule in self._rules:
            if rule.predicate(parsed, analysis, config):
                return rule.strategy

        if analysis.structure_score >= config.structure_score_threshold:
            return ChunkingStrategy.STRUCTURE
        return ChunkingStrategy.SEMANTIC


def _default_rules() -> list[SelectorRule]:
    return [
        SelectorRule(
            name="low_ocr_quality",
            priority=5,
            predicate=lambda parsed, analysis, config: (
                parsed.ocr_quality is not None
                and parsed.ocr_quality < config.ocr_confidence_threshold
            ),
            strategy=ChunkingStrategy.SEMANTIC,
        ),
        SelectorRule(
            name="mixed_or_low_language_confidence",
            priority=8,
            predicate=lambda parsed, _analysis, _config: (
                parsed.structure_hints.get("is_mixed") is True
                or (
                    isinstance(parsed.structure_hints.get("language_confidence"), (int, float))
                    and float(parsed.structure_hints["language_confidence"]) < 0.6
                )
                or parsed.language == "mixed"
            ),
            strategy=ChunkingStrategy.SEMANTIC,
        ),
        SelectorRule(
            name="markdown",
            priority=10,
            predicate=lambda parsed, analysis, _config: (
                parsed.source_format is SourceFormat.MARKDOWN and analysis.signals.markdown_detected
            ),
            strategy=ChunkingStrategy.MARKDOWN,
        ),
        SelectorRule(
            name="docx_headings",
            priority=20,
            predicate=lambda parsed, analysis, _config: (
                parsed.source_format is SourceFormat.DOCX and analysis.signals.has_headings
            ),
            strategy=ChunkingStrategy.HEADING,
        ),
        SelectorRule(
            name="structured_pdf",
            priority=30,
            predicate=lambda parsed, analysis, config: (
                parsed.source_format is SourceFormat.PDF
                and analysis.structure_score >= config.structure_score_threshold
            ),
            strategy=ChunkingStrategy.STRUCTURE,
        ),
        SelectorRule(
            name="structured_html",
            priority=40,
            predicate=lambda parsed, analysis, config: (
                parsed.source_format is SourceFormat.HTML
                and analysis.structure_score >= config.structure_score_threshold
            ),
            strategy=ChunkingStrategy.STRUCTURE,
        ),
        SelectorRule(
            name="structured_generic",
            priority=50,
            predicate=lambda _parsed, analysis, config: (
                analysis.structure_score >= config.structure_score_threshold
            ),
            strategy=ChunkingStrategy.STRUCTURE,
        ),
        SelectorRule(
            name="weak_structure",
            priority=100,
            predicate=lambda _parsed, _analysis, _config: True,
            strategy=ChunkingStrategy.SEMANTIC,
        ),
    ]
