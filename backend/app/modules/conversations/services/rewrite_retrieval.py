"""Bounded recall of prior citations for explicit, fact-preserving rewrites."""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.modules.conversations.ports import ContextRetrievalResult, RetrievalPort
from app.modules.conversations.turn_resolution import (
    FollowupMode,
    HistoryMessage,
    TurnOutcome,
    TurnRelation,
)

_PRESENTATION_ACTION = re.compile(
    r"\b(?:summari[sz]e|shorten|translate|rewrite|rephrase|simplify|format|table|bullets?|"
    r"shorter|concise)\b|বুলেট|সংক্ষেপ|সংক্ষিপ্ত|বাংলায়|বাংলায়|সহজ|ছোট",
    re.I,
)
_REFERENCE = re.compile(
    r"\b(?:previous|earlier|last|above|this|that|it|answer)\b|আগের|পূর্বের|এটি|এটা|সেটি|উত্তরটি",
    re.I,
)
_ADDED_FACT = re.compile(
    r"\b(?:add|compare|update)\b|"
    r"\binclude\b(?!\s+(?:the\s+)?(?:same\s+)?(?:citations?|sources?|references?))|"
    r"\b(?:current|latest)\s+(?:fees?|penalt(?:y|ies)|rules?|rates?|requirements?)\b|"
    r"(?:আরও|যোগ|বর্তমান|নতুন|তুলনা).{0,40}(?:তথ্য|ফি|জরিমানা|নিয়ম|নিয়ম|হার)",
    re.I,
)
_CITATION_MARKER = re.compile(r"\[(\d+)\]")


def used_citation_items(content: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return citation snapshots whose numbered markers appear in visible content."""
    used_positions = {
        int(match)
        for match in _CITATION_MARKER.findall(content)
        if 1 <= int(match) <= len(citations)
    }
    return [
        citation
        for position, citation in enumerate(citations, start=1)
        if position in used_positions and isinstance(citation, dict)
    ]


def rewrite_followup_mode(
    question: str,
    outcome: TurnOutcome,
    relation: TurnRelation,
    resolved_mode: FollowupMode = FollowupMode.NOT_APPLICABLE,
) -> FollowupMode:
    """Resolve rewrite intent while keeping explicit presentation wording authoritative."""
    if outcome is not TurnOutcome.RESOLVED or relation is not TurnRelation.FOLLOW_UP:
        return FollowupMode.NOT_APPLICABLE
    presentation_action = bool(_PRESENTATION_ACTION.search(question))
    if presentation_action and _ADDED_FACT.search(question):
        return FollowupMode.ADDS_FACTS
    if presentation_action and (_REFERENCE.search(question) or len(question.split()) <= 12):
        return FollowupMode.PRESENTATION_ONLY
    return resolved_mode


def retained_rewrite_question(history: list[HistoryMessage]) -> str | None:
    """Find the latest factual user topic across chains of presentation-only rewrites."""
    for item in reversed(history):
        if item.role != "user":
            continue
        mode = rewrite_followup_mode(
            item.content,
            TurnOutcome.RESOLVED,
            TurnRelation.FOLLOW_UP,
        )
        if mode is not FollowupMode.PRESENTATION_ONLY:
            return item.content
    return None


def rewrite_citation_ids(
    question: str,
    outcome: TurnOutcome,
    relation: TurnRelation,
    history: list[HistoryMessage],
    citations_by_message: dict[uuid.UUID, list[dict[str, Any]]],
    *,
    mode: FollowupMode = FollowupMode.NOT_APPLICABLE,
) -> list[uuid.UUID]:
    # ChatService may have already recovered an explicit rewrite intent after a
    # conservative resolver result. Preserve that bounded decision here instead
    # of recomputing it from the resolver's original relation.
    mode = (
        rewrite_followup_mode(question, outcome, relation, mode)
        if mode is FollowupMode.NOT_APPLICABLE
        else rewrite_followup_mode(
            question,
            TurnOutcome.RESOLVED,
            TurnRelation.FOLLOW_UP,
            mode,
        )
    )
    if mode is FollowupMode.NOT_APPLICABLE:
        return []
    previous = next((item for item in reversed(history) if item.role == "assistant"), None)
    if previous is None:
        return []
    citations = citations_by_message.get(previous.id, [])
    ids = []
    for citation in used_citation_items(previous.content, citations):
        # Assistant messages retain all passages supplied to generation for
        # inspection. Only markers rendered in the visible answer are citations
        # that a presentation-only follow-up may reuse.
        if citation.get("source_kind") == "web":
            continue
        try:
            identifier = uuid.UUID(str(citation.get("chunk_id")))
        except (ValueError, TypeError):
            continue
        if identifier not in ids:
            ids.append(identifier)
    return ids[:24]


async def retrieve_rewrite_context(
    retrieval: RetrievalPort,
    *,
    seeds: list[uuid.UUID],
    request: dict[str, Any],
    mode: FollowupMode = FollowupMode.PRESENTATION_ONLY,
) -> ContextRetrievalResult:
    if not seeds and mode is FollowupMode.PRESENTATION_ONLY:
        bounded_request = dict(request)
        bounded_request["top_k"] = min(int(request.get("top_k") or 3), 3)
        result = await retrieval.retrieve(**bounded_request)
        result.diagnostics["rewrite_recall"] = {
            "status": "bounded_fallback_search_no_citations",
            "seed_count": 0,
            "recalled_count": 0,
            "missing_seed_count": 0,
            "mode": mode.value,
        }
        return result
    if not seeds or getattr(retrieval, "supports_cited_retrieval", False) is not True:
        return await retrieval.retrieve(**request)
    cited = await retrieval.retrieve(**request, cited_chunk_ids=seeds)
    returned_ids = {chunk.chunk_id for chunk in cited.chunks}
    missing_seed_count = sum(seed not in returned_ids for seed in seeds)
    needs_search = mode is FollowupMode.ADDS_FACTS or not cited.chunks or missing_seed_count > 0
    if needs_search:
        # Source IDs can disappear after a rebuild. Re-search under the same
        # requested filters and fill mixed/new facets from the active snapshot.
        search_request = dict(request)
        if mode is FollowupMode.PRESENTATION_ONLY:
            search_request["top_k"] = min(
                int(request.get("top_k") or max(3, len(seeds))),
                max(3, len(seeds)),
            )
        searched = await retrieval.retrieve(**search_request)
        top_k = int(request.get("top_k") or len(cited.chunks) + len(searched.chunks))
        if mode is FollowupMode.ADDS_FACTS and cited.chunks:
            cited_slots = max(1, top_k // 2)
            ordered = [*cited.chunks[:cited_slots], *searched.chunks]
        else:
            ordered = [*cited.chunks, *searched.chunks]
        chunks = []
        seen: set[uuid.UUID] = set()
        for chunk in ordered:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            chunks.append(chunk)
            if len(chunks) == top_k:
                break
        result = ContextRetrievalResult(chunks=chunks, diagnostics=dict(searched.diagnostics))
    else:
        result = cited
    result.diagnostics["rewrite_recall"] = {
        "status": (
            "mixed_cited_and_search"
            if mode is FollowupMode.ADDS_FACTS
            else "bounded_fallback_search"
            if not cited.chunks
            else "partial_fallback_search"
            if missing_seed_count
            else "cited_passages"
        ),
        "seed_count": len(seeds),
        "recalled_count": len(returned_ids),
        "missing_seed_count": missing_seed_count,
        "mode": mode.value,
    }
    return result
