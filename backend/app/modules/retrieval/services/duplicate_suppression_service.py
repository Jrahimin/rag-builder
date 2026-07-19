"""Conservative final-result diversity controls for retrieval context."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.core.config import RetrievalConfig
from app.modules.retrieval.schemas.search import RetrievalResult
from app.platform.domain.content_hash import content_hash
from app.platform.domain.text_normalizer import normalize_for_indexing


@dataclass(frozen=True, slots=True)
class DuplicateSuppressionResult:
    """Selected results plus sanitized suppression diagnostics."""

    results: list[RetrievalResult]
    input_count: int
    suppressed_by_reason: dict[str, int]

    @property
    def suppressed_count(self) -> int:
        return sum(self.suppressed_by_reason.values())


class DuplicateSuppressionService:
    """Keep ranked order while limiting repeated documents, sections, and text."""

    def __init__(self, config: RetrievalConfig) -> None:
        self._config = config

    def select(
        self,
        results: list[RetrievalResult],
        *,
        limit: int,
    ) -> DuplicateSuppressionResult:
        selected: list[RetrievalResult] = []
        seen_hashes: set[str] = set()
        document_counts: Counter[object] = Counter()
        section_counts: Counter[tuple[object, str]] = Counter()
        suppressed: Counter[str] = Counter()

        for result in results:
            if len(selected) >= limit:
                break

            digest = content_hash(normalize_for_indexing(result.content))
            if self._config.deduplicate_by_content_hash and digest in seen_hashes:
                suppressed["content_hash"] += 1
                continue
            if document_counts[result.document_id] >= self._config.max_chunks_per_document:
                suppressed["document_limit"] += 1
                continue

            section_title = result.metadata.get("section_title")
            section_key: tuple[object, str] | None = None
            if isinstance(section_title, str) and section_title.strip():
                section_key = (result.document_id, section_title.strip())
                if section_counts[section_key] >= self._config.max_chunks_per_section:
                    suppressed["section_limit"] += 1
                    continue

            selected.append(result)
            seen_hashes.add(digest)
            document_counts[result.document_id] += 1
            if section_key is not None:
                section_counts[section_key] += 1

        return DuplicateSuppressionResult(
            results=selected,
            input_count=len(results),
            suppressed_by_reason=dict(sorted(suppressed.items())),
        )
