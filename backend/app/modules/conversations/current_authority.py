"""Provision-scoped removal of superseded text before evidence admission."""

from __future__ import annotations

from dataclasses import replace

import regex

from app.modules.conversations.ports import ContextChunk
from app.platform.domain.content_hash import content_hash

_PROVISION_HEADING = regex.compile(
    r"^(?:section|article|rule|regulation|§)\s+\p{Number}[\p{Number}A-Za-z()./-]*\b.*$",
    regex.IGNORECASE,
)
_ENFORCEABLE_OUTCOMES = {"expanded", "already_in_recall"}


def remove_superseded_provisions(chunks: list[ContextChunk]) -> list[ContextChunk]:
    """Redact only explicitly scoped base provisions with a recalled modifier.

    Unscoped relationships and headings that cannot be resolved exactly are
    intentionally left untouched. This is fail-closed for authority metadata:
    document-level MODIFIES never implies whole-document invalidation.
    """
    present_revisions = {
        str(value)
        for chunk in chunks
        if (value := chunk.metadata.get("source_revision_id")) is not None
    }
    records = _records(chunks)
    scopes_by_base: dict[str, set[str]] = {}
    for record in records:
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
    return output


def _records(chunks: list[ContextChunk]) -> list[dict[str, object]]:
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
