"""Provision-scoped removal of superseded text before evidence admission."""

from __future__ import annotations

from dataclasses import replace

import regex

from app.modules.conversations.ports import ContextChunk
from app.platform.domain.content_hash import content_hash

_PROVISION_HEADING = regex.compile(
    r"^(?:section|article|rule|regulation|§|ধারা|বিধি)\s+"
    r"\p{Number}[\p{Number}A-Za-z()./-]*\b.*$",
    regex.IGNORECASE,
)
_ENFORCEABLE_OUTCOMES = {"expanded", "already_in_recall"}


def remove_superseded_provisions(
    chunks: list[ContextChunk],
    expansion_records: list[dict[str, object]] | None = None,
) -> list[ContextChunk]:
    """Redact only explicitly scoped base provisions with a recalled modifier.

    ``expansion_records`` should come from the top-level retrieval diagnostics
    (``SearchDiagnostics.modifies_expansion_records`` or the equivalent dict
    from ``retrieval_result.diagnostics``).  When not supplied the function
    falls back to reading the list from the first chunk that carries it
    (legacy path; kept so callers that have not yet been migrated continue to
    work but no new callers should rely on it).

    Unscoped relationships and headings that cannot be resolved exactly are
    intentionally left untouched.  This is fail-closed for authority metadata:
    document-level MODIFIES never implies whole-document invalidation. Unresolved
    relationships are retained as source text with an explicit authority limitation.
    """
    if expansion_records is None:
        expansion_records = _records_from_chunks(chunks)

    present_revisions = {
        str(value)
        for chunk in chunks
        if (value := chunk.metadata.get("source_revision_id")) is not None
    }
    scopes_by_base: dict[str, set[str]] = {}
    for record in expansion_records:
        if str(record.get("outcome")) not in _ENFORCEABLE_OUTCOMES:
            continue
        if str(record.get("modifier_revision_id")) not in present_revisions:
            continue
        provisions = record.get("target_provisions")
        if not isinstance(provisions, list) or not provisions:
            continue
        base_revision = str(record.get("base_revision_id") or "")
        if not base_revision:
            continue
        scopes_by_base.setdefault(base_revision, set()).update(
            item.strip() for item in provisions if isinstance(item, str) and item.strip()
        )

    output: list[ContextChunk] = []
    for chunk in chunks:
        revision = str(chunk.metadata.get("source_revision_id") or "")
        scopes = scopes_by_base.get(revision)
        if not scopes:
            output.append(chunk)
            continue
        redacted, resolved = _redact_exact_provisions(chunk.content, scopes)
        if not resolved:
            output.append(chunk)
            continue
        if not redacted.strip():
            # Whole chunk is superseded; absent from admission.
            continue
        output.append(
            replace(
                chunk,
                content=redacted,
                chunk_hash=content_hash(redacted),
                metadata={
                    **chunk.metadata,
                    "authority_redaction": "scoped_provision",
                    "authority_redacted_provisions": sorted(resolved),
                },
            )
        )
    return annotate_authority_limitations(output, expansion_records)


def annotate_authority_limitations(
    chunks: list[ContextChunk], records: list[dict[str, object]]
) -> list[ContextChunk]:
    """Do not mistake failure to prove supersession for proof of current authority.

    Run again after budgeting: recall of a modifier does not guarantee that its
    text survived admission/selection. No tax vocabulary, date guessing or
    document-wide invalidation is used here.
    """
    irrelevant = {
        "inactive",
        "outside_as_of",
        "stale_or_replaced_revision",
        "cross_project_or_generation",
    }
    present = {str(c.metadata.get("source_revision_id") or "") for c in chunks}
    output: list[ContextChunk] = []
    for chunk in chunks:
        revision = str(chunk.metadata.get("source_revision_id") or "")
        limitations: list[dict[str, object]] = []
        table_status = chunk.metadata.get("table_context_status")
        is_table = chunk.metadata.get("element_type") == "table"
        if table_status == "context_exceeds_budget" or (
            is_table and table_status != "preserved" and not chunk.metadata.get("section_title")
        ):
            limitations.append({"reason": "table_applicability_context_missing"})
        for record in records:
            if record.get("relationship_type", "modifies") != "modifies":
                continue
            if not revision or str(record.get("base_revision_id") or "") != revision:
                continue
            outcome = str(record.get("outcome") or "")
            if outcome in irrelevant:
                continue
            scopes = record.get("target_provisions")
            if _explicitly_disjoint_provisions(chunk.content, scopes):
                continue
            redacted = set(chunk.metadata.get("authority_redacted_provisions") or [])
            if (
                isinstance(scopes, list)
                and scopes
                and all(isinstance(scope, str) for scope in scopes)
                and set(scopes) <= redacted
            ):
                continue
            if outcome not in _ENFORCEABLE_OUTCOMES | {"duplicate"}:
                reason = outcome or "unresolved_relationship"
            elif not scopes:
                reason = "missing_provision_scope"
            elif str(record.get("modifier_revision_id") or "") not in present:
                reason = "modifier_absent_from_context"
            else:
                reason = "provision_scope_not_resolved"
            limitations.append(
                {
                    "reason": reason,
                    "relationship_id": record.get("relationship_id"),
                    "modifier_revision_id": record.get("modifier_revision_id"),
                    "target_provisions": scopes or [],
                }
            )
        metadata = dict(chunk.metadata)
        if limitations:
            metadata.update(authority_status="unresolved", authority_limitations=limitations)
        output.append(replace(chunk, metadata=metadata))
    return output


# ---------------------------------------------------------------------------
# Backward-compat helper: read records from chunk metadata when the caller
# has not yet been updated to pass them from the top-level diagnostics.
# No new callers should use this path.
# ---------------------------------------------------------------------------


def _records_from_chunks(chunks: list[ContextChunk]) -> list[dict[str, object]]:
    for chunk in chunks:
        value = chunk.metadata.get("modifies_expansion_records")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _redact_exact_provisions(content: str, scopes: set[str]) -> tuple[str, set[str]]:
    lines = content.splitlines(keepends=True)
    headings = [
        index for index, line in enumerate(lines) if _PROVISION_HEADING.fullmatch(line.strip())
    ]
    normalized_scopes = {_normalize_heading(scope): scope for scope in scopes}
    ranges: list[tuple[int, int]] = []
    resolved: set[str] = set()
    for position, line_index in enumerate(headings):
        normalized = _normalize_heading(lines[line_index])
        scope = normalized_scopes.get(normalized)
        if scope is None:
            continue
        end = headings[position + 1] if position + 1 < len(headings) else len(lines)
        ranges.append((line_index, end))
        resolved.add(scope)
    for start, end in ranges:
        for index in range(start, end):
            lines[index] = "".join(char if char in {"\r", "\n"} else " " for char in lines[index])
    return "".join(lines), resolved


def _normalize_heading(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def _explicitly_disjoint_provisions(content: str, scopes: object) -> bool:
    """Only complete headed passages can prove a scoped amendment unrelated.

    Unheaded continuations, unknown scope labels and document-wide links still
    require evidence review. Subsections share the parent provision identity.
    """
    if not isinstance(scopes, list) or not scopes:
        return False
    pattern = regex.compile(
        r"^(section|article|rule|regulation|§|ধারা|বিধি)\s+(\p{Number}+[A-Za-z]?)\b",
        regex.IGNORECASE,
    )

    def key(text: str) -> tuple[str, str] | None:
        # A range/list is not a single provision. Do not narrow its meaning.
        if regex.search(
            r"\p{Number}\s*[-\u2013/,]\s*\p{Number}|\b(?:and|to|through)\s+\p{Number}",
            text,
            regex.IGNORECASE,
        ):
            return None
        match = pattern.match(text.strip())
        if not match:
            return None
        kind, number = match.groups()
        kind = {"§": "section", "ধারা": "section", "বিধি": "rule"}.get(kind, kind.casefold())
        number = "".join(str(int(c)) if c.isdecimal() else c.casefold() for c in number)
        return kind, number

    lines = content.strip().splitlines()
    if not lines or not _PROVISION_HEADING.fullmatch(lines[0].strip()):
        return False
    if any(regex.match(r"^\s*\p{Number}+[.)]\s", line) for line in lines[1:]):
        return False
    targets = [key(scope) if isinstance(scope, str) else None for scope in scopes]
    if None in targets:
        return False
    headings = [key(line) for line in lines if _PROVISION_HEADING.fullmatch(line.strip())]
    return bool(headings) and None not in headings and set(headings).isdisjoint(targets)
