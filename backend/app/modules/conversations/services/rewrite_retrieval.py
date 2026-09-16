"""Bounded recall of prior citations for explicit, fact-preserving rewrites."""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.modules.conversations.ports import ContextRetrievalResult, RetrievalPort
from app.modules.conversations.turn_resolution import HistoryMessage, TurnOutcome, TurnRelation


def rewrite_citation_ids(
    question: str,
    outcome: TurnOutcome,
    relation: TurnRelation,
    history: list[HistoryMessage],
    citations_by_message: dict[uuid.UUID, list[dict[str, Any]]],
) -> list[uuid.UUID]:
    if outcome is not TurnOutcome.RESOLVED or relation is not TurnRelation.FOLLOW_UP:
        return []
    if not all(
        re.search(pattern, question, re.I)
        for pattern in (
            r"\b(previous|earlier|last|above|this|that|it)\b|আগের|পূর্বের|এটি|এটা|সেটি|উত্তরটি",
            r"\b(summari[sz]e|shorten|translate|rewrite|rephrase|bullets?)\b|বুলেট|সংক্ষেপ|সংক্ষিপ্ত|বাংলায়|বাংলায়|সহজ",
        )
    ):
        return []
    # Ordinary rewrites should not require a magic "no new facts" phrase.
    # Mixed requests still need broad retrieval for their added subject matter.
    normalized = re.sub(
        r"\b(no new|do not add|don't add|without adding)\b[^.!?]*|নতুন তথ্য[^।.!?]*না[।.!?]?",
        "",
        question,
        flags=re.I,
    )
    if re.search(
        r"\b(add|also|include new|update|current|latest|compare)\b|আরও|যোগ|বর্তমান|নতুন|তুলনা",
        normalized,
        re.I,
    ):
        return []
    previous = next((item for item in reversed(history) if item.role == "assistant"), None)
    if previous is None:
        return []
    ids = []
    for citation in citations_by_message.get(previous.id, []):
        if not isinstance(citation, dict) or citation.get("source_kind") == "web":
            continue
        try:
            identifier = uuid.UUID(str(citation.get("chunk_id")))
        except (ValueError, TypeError):
            continue
        if identifier not in ids:
            ids.append(identifier)
    return ids[:24]


async def retrieve_rewrite_context(
    retrieval: RetrievalPort, *, seeds: list[uuid.UUID], request: dict[str, Any]
) -> ContextRetrievalResult:
    if not seeds or getattr(retrieval, "supports_cited_retrieval", False) is not True:
        return await retrieval.retrieve(**request)
    result = await retrieval.retrieve(**request, cited_chunk_ids=seeds)
    fallback = not result.chunks
    if fallback:
        # Source IDs can disappear after a rebuild. Re-search under the same
        # requested filters; never reuse stale excerpts from message history.
        result = await retrieval.retrieve(**request)
    result.diagnostics["rewrite_recall"] = {
        "status": "fallback_search" if fallback else "cited_passages",
        "seed_count": len(seeds),
    }
    return result
