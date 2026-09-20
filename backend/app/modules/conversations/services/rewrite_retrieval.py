"""Bounded recall of prior citations for explicit, fact-preserving rewrites."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from app.modules.conversations.citation_snapshots import (
    EVIDENCE_PROVENANCE_VERSION,
    REUSE_VALIDATION_VERSION,
    citation_authority_records,
)
from app.modules.conversations.context_builder import comparison_requested
from app.modules.conversations.ports import ContextRetrievalResult, RetrievalPort
from app.modules.conversations.turn_resolution import (
    FollowupMode,
    HistoryMessage,
    RequestFilters,
    TurnOutcome,
    TurnRelation,
    TurnResolutionInput,
)
from app.modules.conversations.turn_resolver import ResolvedTurn, presentation_followup_resolution

_PRESENTATION_ACTION = re.compile(
    r"\b(?:summari[sz]e|shorten|translate|rewrite|rephrase|simplify|format|table|bullets?|"
    r"shorter|concise)\b|বুলেট|সংক্ষেপ|সংক্ষিপ্ত|বাংলায়|বাংলায়|সহজ|ছোট",
    re.I,
)
_REFERENCE = re.compile(
    r"\b(?:previous|earlier|last|above|this|that|it|answer)\b|আগের|পূর্বের|এটি|এটা|সেটি|উত্তরটি",
    re.I,
)
_KEEP_ORIGINAL = re.compile(
    r"\b(?:keep|same|citations?|sources?|references?|facts?|original)\b|"
    r"মূল|রাখুন|সূত্র|তথ্য|"
    r"নতুন তথ্য যোগ করবেন না|যোগ করবেন না",
    re.I,
)
_FORMAT_INSTRUCTION = re.compile(
    r"\b(?:bullets?|table|markdown|english|bengali|bangla|points?|list|"
    r"three|two|four|five|short(?:er)?|concise|brief|please|also|just|only|"
    r"make|into|exactly)\b|"
    r"বুলেট|বাংলায়|বাংলায়|বাংলা|তিনটি|সংক্ষিপ্ত|সহজ|ছোট|বলুন|দিন|সঙ্গে",
    re.I,
)
_RESIDUAL_FACT = re.compile(
    r"\b(?:instead|explain)\b|"
    r"\b(?:20\d{2}|19\d{2})\b|"
    r"পরিবর্তে|ব্যাখ্যা",
    re.I,
)
_FILLER_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "do",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "without",
        "not",
        "new",
        "anything",
        "এবং",
        "ও",
    }
)
_ADDED_FACT = re.compile(
    r"\b(?:add|compare|update)\b|"
    r"\binclude\b(?!\s+(?:the\s+)?(?:same\s+)?(?:citations?|sources?|references?))|"
    r"\b(?:calculat(?:e|ed|ing|ion|ions)|recalculat(?:e|ion)|"
    r"comput(?:e|ing|ation)|breakdown)\b|\bhow much\b|"
    r"\b(?:current|latest)\s+(?:fees?|penalt(?:y|ies)|rules?|rates?|requirements?)\b|"
    r"\b(?:still apply|now apply|currently apply|still applicable)\b|"
    r"(?:আরও|যোগ|বর্তমান|নতুন|তুলনা).{0,40}(?:তথ্য|ফি|জরিমানা|নিয়ম|নিয়ম|হার)|"
    r"হিসাব|হিসেব|গণনা|পরিগণনা",
    re.I,
)
_NEGATED_ADDITION = re.compile(
    r"\b(?:do\s+not|don't|dont|without)\s+add\b|"
    r"নতুন তথ্য যোগ করবেন না|যোগ করবেন না",
    re.I,
)
_CALCULATION_REQUEST = re.compile(
    r"\b(?:calculat(?:e|ed|ing|ion|ions)|recalculat(?:e|ion)|"
    r"comput(?:e|ing|ation)|breakdown)\b|\bhow much\b|হিসাব|হিসেব|গণনা|পরিগণনা",
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
    remainder = _NEGATED_ADDITION.sub(" ", question)
    added_facts = bool(_ADDED_FACT.search(remainder))
    if presentation_action and added_facts:
        return FollowupMode.ADDS_FACTS
    if presentation_action and _residual_factual_request(question):
        # Unknown audience/topic/applicability wording is not eligible for the
        # deterministic shortcut.  Once the resolver has considered conversation
        # context, however, preserve its decision instead of applying this weaker
        # lexical heuristic a second time.
        return resolved_mode
    if presentation_action and (_REFERENCE.search(question) or len(question.split()) <= 12):
        return FollowupMode.PRESENTATION_ONLY
    return resolved_mode


def _residual_factual_request(question: str) -> bool:
    """True when leftover wording still names a topic, date, population, or applicability."""
    if _RESIDUAL_FACT.search(question):
        return True
    remainder = _NEGATED_ADDITION.sub(" ", question)
    remainder = _PRESENTATION_ACTION.sub(" ", remainder)
    remainder = _REFERENCE.sub(" ", remainder)
    remainder = _KEEP_ORIGINAL.sub(" ", remainder)
    remainder = _FORMAT_INSTRUCTION.sub(" ", remainder)
    remainder = re.sub(r"[^\w\u0980-\u09FF]+", " ", remainder, flags=re.UNICODE)
    tokens = [
        token
        for token in remainder.split()
        if token.casefold() not in _FILLER_TOKENS
        and any(unicodedata.category(char)[0] in {"L", "N"} for char in token)
    ]
    # Shortness is not evidence that facts are unchanged: a single word can name
    # a new topic ("penalties") or population ("partnerships" / "minors").
    return bool(tokens)


def retained_rewrite_question(
    history: list[HistoryMessage],
    assistant_metadata_by_id: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """Find the latest factual user topic across chains of presentation-only rewrites."""
    metadata = assistant_metadata_by_id or {}
    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        if item.role != "user":
            continue
        following = history[index + 1] if index + 1 < len(history) else None
        if following is not None and following.role == "assistant":
            recorded = metadata.get(str(following.id), {}).get("turn_resolution")
            if isinstance(recorded, dict) and recorded.get("followup_mode") == (
                FollowupMode.PRESENTATION_ONLY.value
            ):
                retained = recorded.get("retained_factual_question")
                if isinstance(retained, str) and retained.strip():
                    return retained.strip()
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
    ids: list[uuid.UUID] = []
    needed_relationships: set[str] = set()
    needed_modifiers: set[str] = set()
    for citation in used_citation_items(previous.content, citations):
        # Visible answer markers identify the passages a presentation-only
        # follow-up may reuse. Recalled modifier identities are seeded from
        # recorded authority_dependencies, not from extra citation slots.
        if citation.get("source_kind") == "web":
            continue
        try:
            identifier = uuid.UUID(str(citation.get("chunk_id")))
        except (ValueError, TypeError):
            continue
        if identifier not in ids:
            ids.append(identifier)
        for record in citation_authority_records(citation):
            if record.get("modifier_recalled") is False:
                continue
            relationship_id = record.get("relationship_id")
            if relationship_id:
                needed_relationships.add(str(relationship_id))
            modifier = record.get("modifier_revision_id")
            if modifier:
                needed_modifiers.add(str(modifier))
            for extra in _recorded_modifier_chunk_ids(record):
                if extra not in ids:
                    ids.append(extra)
    for citation in citations:
        if citation.get("source_kind") == "web":
            continue
        try:
            identifier = uuid.UUID(str(citation.get("chunk_id")))
        except (ValueError, TypeError):
            continue
        if identifier in ids:
            continue
        revision = str(citation.get("source_revision_id") or "")
        related = {
            str(item.get("relationship_id"))
            for item in citation_authority_records(citation)
            if item.get("relationship_id")
        }
        related.update(
            str(item.get("relationship_id"))
            for item in citation.get("relationship_recall_provenance") or []
            if isinstance(item, dict) and item.get("relationship_id")
        )
        if revision in needed_modifiers or needed_relationships & related:
            ids.append(identifier)
    return ids[:24]


def _recorded_modifier_chunk_ids(record: dict[str, Any]) -> list[uuid.UUID]:
    values: list[object] = []
    if record.get("modifier_chunk_id"):
        values.append(record.get("modifier_chunk_id"))
    extra = record.get("modifier_chunk_ids")
    if isinstance(extra, list):
        values.extend(extra)
    ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values:
        try:
            identifier = uuid.UUID(str(value))
        except (ValueError, TypeError):
            continue
        if identifier in seen:
            continue
        seen.add(identifier)
        ids.append(identifier)
    return ids


def preceding_assistant(history: list[HistoryMessage]) -> HistoryMessage | None:
    """Return the latest assistant turn, if any."""
    return next((item for item in reversed(history) if item.role == "assistant"), None)


def used_citations_include_web(content: str, citations: list[dict[str, Any]]) -> bool:
    """True when a visible citation marker refers to a web snapshot."""
    return any(item.get("source_kind") == "web" for item in used_citation_items(content, citations))


def saved_evidence_scope(citations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the shared request-scope recorded on reused knowledge citations."""
    saved: dict[str, Any] | None = None
    for citation in citations:
        if citation.get("source_kind") == "web":
            continue
        if citation.get("evidence_provenance_version") != EVIDENCE_PROVENANCE_VERSION:
            continue
        metadata = citation.get("evidence_scope_metadata_filter")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            return None
        current = {
            "document_id": citation.get("evidence_scope_document_id"),
            "metadata_filter": {str(key): str(value) for key, value in metadata.items()},
            "as_of": citation.get("evidence_scope_as_of"),
            "snapshot_origin": citation.get("evidence_scope_snapshot_origin"),
        }
        if saved is None:
            saved = current
            continue
        if (
            str(saved.get("document_id") or "") != str(current.get("document_id") or "")
            or dict(saved.get("metadata_filter") or {}) != current["metadata_filter"]
            or _as_of_instant(saved.get("as_of")) != _as_of_instant(current.get("as_of"))
        ):
            return None
    return saved


def citation_scopes_conflict(citations: list[dict[str, Any]]) -> bool:
    """True when reused knowledge citations disagree about request scope."""
    seen: list[tuple[str, tuple[tuple[str, str], ...], datetime | None]] = []
    for citation in citations:
        if citation.get("source_kind") == "web":
            continue
        if citation.get("evidence_provenance_version") != EVIDENCE_PROVENANCE_VERSION:
            continue
        metadata = citation.get("evidence_scope_metadata_filter")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            return True
        key = (
            str(citation.get("evidence_scope_document_id") or ""),
            tuple(sorted((str(key), str(value)) for key, value in metadata.items())),
            _as_of_instant(citation.get("evidence_scope_as_of")),
        )
        if seen and key != seen[0]:
            return True
        if not seen:
            seen.append(key)
    return False


def request_scope_conflicts(request: RequestFilters, saved: dict[str, Any] | None) -> bool:
    """True when an explicit request filter differs from saved evidence scope."""
    if saved is None:
        return False
    request_document = str(request.document_id) if request.document_id is not None else None
    saved_document = str(saved["document_id"]) if saved.get("document_id") is not None else None
    if request_document is not None and request_document != saved_document:
        return True
    request_metadata = _stringify_filter(request.metadata_filter or {})
    saved_metadata = _stringify_filter(saved.get("metadata_filter") or {})
    if request_metadata and request_metadata != saved_metadata:
        return True
    request_as_of = _as_of_instant(request.as_of)
    if request_as_of is None:
        return False
    return request_as_of != _as_of_instant(saved.get("as_of"))


def _stringify_filter(metadata: object) -> dict[str, str]:
    if not isinstance(metadata, dict):
        return {}
    return {str(key): str(value) for key, value in metadata.items()}


def _as_of_instant(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def citations_have_exact_reuse_provenance(citations: list[dict[str, Any]]) -> bool:
    """True when every knowledge citation has the hashes and spans exact reuse needs."""
    knowledge = False
    for citation in citations:
        if citation.get("source_kind") == "web":
            continue
        knowledge = True
        if citation.get("evidence_provenance_version") != EVIDENCE_PROVENANCE_VERSION:
            return False
        if not citation.get("indexed_chunk_hash") or not citation.get("evidence_span_hash"):
            return False
        start = citation.get("evidence_chunk_char_start")
        end = citation.get("evidence_chunk_char_end")
        if not isinstance(start, int) or not isinstance(end, int):
            return False
    return knowledge


def inherited_as_of(request: RequestFilters, saved: dict[str, Any] | None) -> datetime | None:
    """Reuse an unchanged historical cutoff without treating it as a new request filter."""
    if saved is None or request.as_of is not None:
        return None
    value = saved.get("as_of")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def try_presentation_preflight(
    payload: TurnResolutionInput,
    *,
    citations_by_message: dict[uuid.UUID, list[dict[str, Any]]],
) -> ResolvedTurn | None:
    """Build a validated presentation follow-up without calling the resolver LLM.

    Unknown wording returns None so the existing resolver runs once. A later
    evidence-validation failure must not call this again to reclassify intent.
    Mixed web citations and a changed request scope still yield this follow-up;
    ChatService then exits exact indexed reuse instead of omitting those sources.
    """
    previous = preceding_assistant(payload.history)
    if previous is None:
        return None
    mode = rewrite_followup_mode(
        payload.current_message,
        TurnOutcome.RESOLVED,
        TurnRelation.FOLLOW_UP,
    )
    if mode is not FollowupMode.PRESENTATION_ONLY:
        return None
    if comparison_requested(payload.current_message) or _CALCULATION_REQUEST.search(
        payload.current_message
    ):
        return None
    if retained_rewrite_question(payload.history) is None:
        return None
    used = used_citation_items(previous.content, citations_by_message.get(previous.id, []))
    if citation_scopes_conflict(used):
        return None
    saved = saved_evidence_scope(used)
    inherited_document = _optional_uuid(saved.get("document_id") if saved else None)
    inherited_metadata = (
        dict(saved.get("metadata_filter") or {})
        if saved is not None and payload.request_filters.metadata_filter == {}
        else None
    )
    if payload.request_filters.document_id is not None:
        inherited_document = None
    return presentation_followup_resolution(
        payload,
        reason="deterministic_presentation_rewrite",
        inherited_as_of=inherited_as_of(payload.request_filters, saved),
        inherited_snapshot_origin=str(saved.get("snapshot_origin") or "") if saved else None,
        inherited_document_id=inherited_document,
        inherited_metadata_filter=inherited_metadata,
    )


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def retrieve_rewrite_context(
    retrieval: RetrievalPort,
    *,
    seeds: list[uuid.UUID],
    request: dict[str, Any],
    mode: FollowupMode = FollowupMode.PRESENTATION_ONLY,
    prefer_exact: bool = True,
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
    exact = getattr(retrieval, "retrieve_exact", None)
    if (
        prefer_exact
        and mode is FollowupMode.PRESENTATION_ONLY
        and getattr(retrieval, "supports_exact_recall", False) is True
        and callable(exact)
    ):
        cited = await exact(
            chunk_ids=seeds,
            query=str(request.get("query") or ""),
            document_id=request.get("document_id"),
            metadata_filter=request.get("metadata_filter"),
            as_of=request.get("as_of"),
        )
        returned_ids = {chunk.chunk_id for chunk in cited.chunks}
        missing_seed_count = sum(seed not in returned_ids for seed in seeds)
        cited.diagnostics["rewrite_recall"] = {
            "status": (
                "exact_cited_passages"
                if cited.chunks and missing_seed_count == 0
                else "exact_recall_incomplete"
            ),
            "seed_count": len(seeds),
            "recalled_count": len(returned_ids),
            "missing_seed_count": missing_seed_count,
            "mode": mode.value,
            "reuse_validation_version": REUSE_VALIDATION_VERSION,
        }
        return cited
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
