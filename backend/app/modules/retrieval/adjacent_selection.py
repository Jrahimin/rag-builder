"""Rank structurally adjacent chunks without treating synthetic pages as proximity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

ADJACENT_LIMIT = 48
MAX_ADJACENT_ANCHORS = 4
NEAR_INDEX_WINDOW = 3
_SYNTHETIC_SOURCE_FORMATS = frozenset({"md", "markdown", "txt", "text", "html"})
_PAGED_SOURCE_FORMATS = frozenset({"pdf"})

RELATION_IMMEDIATE_INDEX = 0
RELATION_SAME_SECTION = 1
RELATION_PAGE = 2
RELATION_NEAR_INDEX = 3


@dataclass(frozen=True, slots=True)
class AdjacentChunkRef:
    """Indexed chunk fields needed to rank structural neighbours."""

    id: uuid.UUID
    document_id: uuid.UUID
    document_version: int
    chunk_index: int
    page_start: int | None = None
    page_end: int | None = None
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def page_value(chunk: AdjacentChunkRef) -> int | None:
    if chunk.page_start is not None:
        return chunk.page_start
    return chunk.page_number


def page_range(chunk: AdjacentChunkRef) -> tuple[int, int] | None:
    start = page_value(chunk)
    if start is None:
        return None
    end = chunk.page_end if chunk.page_end is not None else start
    return (min(start, end), max(start, end))


def heading_key(metadata: dict[str, Any]) -> tuple[str, ...]:
    path = metadata.get("heading_path")
    if isinstance(path, list):
        parts = tuple(str(item).strip() for item in path if str(item).strip())
        if parts:
            return parts
    title = metadata.get("section_title")
    if isinstance(title, str) and title.strip():
        return (title.strip(),)
    return ()


def is_synthetic_page_document(metadata: dict[str, Any]) -> bool:
    """Markdown/text often stamps page 1 on every chunk; that is not pagination."""
    if str(metadata.get("strategy_used") or "") == "markdown":
        return True
    signals = metadata.get("structure_signals")
    if not isinstance(signals, dict):
        return False
    source_format = str(signals.get("source_format") or "").lower()
    if source_format in _SYNTHETIC_SOURCE_FORMATS:
        return True
    return signals.get("markdown_detected") is True and source_format not in _PAGED_SOURCE_FORMATS


def page_provenance_is_meaningful(chunk: AdjacentChunkRef) -> bool:
    """Page neighbours are for real pagination (PDF tables), not synthetic page 1."""
    if page_value(chunk) is None:
        return False
    if is_synthetic_page_document(chunk.metadata):
        return False
    if str(chunk.metadata.get("element_type") or "") == "table":
        return True
    signals = chunk.metadata.get("structure_signals")
    source_format = ""
    if isinstance(signals, dict):
        source_format = str(signals.get("source_format") or "").lower()
    if source_format in _PAGED_SOURCE_FORMATS:
        return True
    if (
        chunk.page_start is not None
        and chunk.page_end is not None
        and chunk.page_start != chunk.page_end
    ):
        return True
    strategy = str(chunk.metadata.get("strategy_used") or "")
    return strategy in {"heading", "structure"} and source_format not in {"", "unknown"}


def structural_relation(
    anchor: AdjacentChunkRef,
    candidate: AdjacentChunkRef,
    *,
    anchor_heading: tuple[str, ...] | None = None,
    candidate_heading: tuple[str, ...] | None = None,
) -> tuple[int, int] | None:
    """Return ``(priority, distance)`` with lower priority closer; ``None`` if unrelated."""
    if candidate.id == anchor.id:
        return None
    if (candidate.document_id, candidate.document_version) != (
        anchor.document_id,
        anchor.document_version,
    ):
        return None
    index_delta = abs(candidate.chunk_index - anchor.chunk_index)
    if index_delta <= 1:
        return (RELATION_IMMEDIATE_INDEX, index_delta)
    if anchor_heading is None:
        anchor_heading = heading_key(anchor.metadata)
    if candidate_heading is None:
        candidate_heading = heading_key(candidate.metadata)
    if anchor_heading and anchor_heading == candidate_heading:
        return (RELATION_SAME_SECTION, index_delta)
    if page_provenance_is_meaningful(anchor) or page_provenance_is_meaningful(candidate):
        anchor_pages = page_range(anchor)
        candidate_pages = page_range(candidate)
        if anchor_pages is not None and candidate_pages is not None:
            gap = max(
                anchor_pages[0] - candidate_pages[1],
                candidate_pages[0] - anchor_pages[1],
                0,
            )
            if gap <= 1:
                return (RELATION_PAGE, gap * 1_000 + index_delta)
    if not anchor_heading and not candidate_heading and index_delta <= NEAR_INDEX_WINDOW:
        return (RELATION_NEAR_INDEX, index_delta)
    return None


def select_adjacent_ids(
    anchors: list[AdjacentChunkRef],
    candidates: list[AdjacentChunkRef],
    *,
    limit: int = ADJACENT_LIMIT,
) -> tuple[uuid.UUID, ...]:
    """Fairly allocate the closest structural neighbours across anchors.

    UUID order is only a tie-break after relation class and distance.
    """
    if not anchors or limit <= 0:
        return ()
    headings = {chunk.id: heading_key(chunk.metadata) for chunk in (*anchors, *candidates)}
    per_anchor: list[list[uuid.UUID]] = []
    for anchor in anchors[:MAX_ADJACENT_ANCHORS]:
        ranked: list[tuple[tuple[int, int, str], uuid.UUID]] = []
        anchor_heading = headings.get(anchor.id, ())
        for candidate in candidates:
            relation = structural_relation(
                anchor,
                candidate,
                anchor_heading=anchor_heading,
                candidate_heading=headings.get(candidate.id, ()),
            )
            if relation is None:
                continue
            priority, distance = relation
            ranked.append(((priority, distance, str(candidate.id)), candidate.id))
        ranked.sort(key=lambda item: item[0])
        per_anchor.append([chunk_id for _, chunk_id in ranked])

    selected: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    index = 0
    while len(selected) < limit:
        progressed = False
        for bucket in per_anchor:
            if index >= len(bucket):
                continue
            chunk_id = bucket[index]
            progressed = True
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            selected.append(chunk_id)
            if len(selected) >= limit:
                break
        if not progressed:
            break
        index += 1
    return tuple(selected)
