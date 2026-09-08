"""Budget trimming for retrieved context chunks."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import replace

from app.core.config import ChatConfig
from app.modules.conversations.ports import ContextChunk, EvidenceUnit


class ContextBuilder:
    """Dedupe and trim already-ranked chunks; does not re-sort or build citations."""

    def __init__(
        self, config: ChatConfig, *, evidence_approach: str = "authoritative", question: str = ""
    ) -> None:
        self._config = config
        self._evidence_approach = evidence_approach
        self._question = question.casefold()

    def select(self, chunks: Sequence[ContextChunk]) -> list[ContextChunk]:
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        selected: list[ContextChunk] = []
        char_budget = self._config.context_char_budget

        if self._evidence_approach == "multi_perspective":
            groups: dict[str, list[ContextChunk]] = {}
            identities = reviewed_work_identities(chunks)
            for chunk in chunks:
                groups.setdefault(identities[chunk.chunk_id], []).append(chunk)

            def requested(group: list[ContextChunk]) -> bool:
                return any(
                    str(c.metadata.get("source_title") or c.filename)
                    .casefold()
                    .removesuffix(".pdf")
                    in self._question
                    for c in group
                )

            groups = dict(sorted(groups.items(), key=lambda item: not requested(item[1])))
            chunks = [
                group[i]
                for i in range(max((len(g) for g in groups.values()), default=0))
                for group in groups.values()
                if len(group) > i
            ]
        for chunk in chunks:
            if chunk.chunk_id in seen_ids or chunk.chunk_hash in seen_hashes:
                continue
            seen_ids.add(chunk.chunk_id)
            seen_hashes.add(chunk.chunk_hash)
            if len(selected) >= self._config.max_context_chunks:
                break
            if char_budget <= 0:
                break
            if isinstance(chunk, EvidenceUnit) and len(chunk.content) > char_budget:
                # An admitted unit is indivisible: budgeting may omit it, never rewrite it.
                continue
            chunk = chunk.restore_applied_rerank_scores()
            if len(chunk.content) > char_budget:
                selected.append(replace(chunk, content=chunk.content[:char_budget]))
                char_budget = 0
            else:
                selected.append(chunk)
                char_budget -= len(chunk.content)

        return selected


def work_identity(chunk: ContextChunk) -> str:
    """Work identity does not assert independent authorship or create replacement edges."""
    return str(
        chunk.metadata.get("work_key")
        or chunk.metadata.get("source_work_key")
        or chunk.metadata.get("source_group_id")
        or chunk.document_id
    )


def reviewed_work_identities(chunks: Sequence[ContextChunk]) -> dict[uuid.UUID, str]:
    # Union identical document copies even when registered with different work keys.
    parents: dict[str, str] = {}
    copies: dict[str, str] = {}

    def root(identity: str) -> str:
        parents.setdefault(identity, identity)
        while parents[identity] != identity:
            parents[identity] = parents[parents[identity]]
            identity = parents[identity]
        return identity

    for chunk in chunks:
        identity = work_identity(chunk)
        root(identity)
        exact_copy = chunk.metadata.get("source_content_hash")
        if exact_copy:
            other = copies.setdefault(str(exact_copy), identity)
            left, right = sorted((root(identity), root(other)))
            parents[right] = left
    return {chunk.chunk_id: root(work_identity(chunk)) for chunk in chunks}


def reviewed_work_count(chunks: Sequence[ContextChunk]) -> int:
    return len(set(reviewed_work_identities(chunks).values()))


def comparison_requested(question: str) -> bool:
    """Lightweight question cue, not an additional model classification call."""
    return bool(
        re.search(
            r"\b(compare|comparison|versus|vs\.?|disagree|perspectives|accounts|differences)\b|তুলনা|মতভেদ|পার্থক্য",
            question,
            re.IGNORECASE,
        )
    )


def historical_scope_requested(question: str, evidence_approach: str) -> bool:
    """Use the existing resolver for an explicit first-turn historical cutoff.

    A publication year in an ordinary factual question is not a source cutoff.
    The resolver, not this cue, validates the exact date or requests clarification.
    """
    if not re.search(r"\b(?:19|20)\d{2}\b|[\u09e7\u09e8][\u09e6-\u09ef]{3}", question):
        return False
    return bool(re.search(r"\bas of\b", question, re.IGNORECASE)) or (
        evidence_approach == "authoritative"
        and bool(
            re.search(
                r"\b(historical|previous|formerly|applied|was|were)\b|পূর্বে|পূর্ববর্তী",
                question,
                re.IGNORECASE,
            )
        )
    )
