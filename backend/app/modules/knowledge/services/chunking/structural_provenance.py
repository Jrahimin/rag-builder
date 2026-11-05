"""Parser identity is carried through transformations and validated once at the end."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from app.modules.knowledge.scope_facts import extract_scope_facts
from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.platform.providers.contracts.document_parser import ParsedDocument


def bind_parser_elements(parsed: ParsedDocument) -> ParsedDocument:
    elements = []
    for index, element in enumerate(parsed.elements):
        start, end = element.char_start, element.char_end
        text = element.text.strip()
        spans = []
        if (
            start is not None
            and end is not None
            and parsed.text[start:end].strip() == text
            and text
            and element.metadata.get("role") not in {"footer", "header", "contents", "boilerplate"}
        ):
            start += len(parsed.text[start:end]) - len(parsed.text[start:end].lstrip())
            end = start + len(text)
            identity = hashlib.sha256(f"{index}:{start}:{end}:{text}".encode()).hexdigest()
            spans = [
                {
                    "element_id": identity,
                    "role": element.element_type.value,
                    "text": text,
                    "char_start": start,
                    "char_end": end,
                    "page_start": element.page_start,
                    "page_end": element.page_end,
                    "provenance": "exact_source_span",
                }
            ]
            if element.element_type.value == "table":
                # The parser's rendered caption/header/body inventory owns row
                # occurrences. Match its complete serialization once, then carry
                # each item by position; identical siblings never select offsets.
                rows = element.metadata.get("table_rows")
                caption = element.metadata.get("table_caption")
                header = element.metadata.get("table_header")
                parts: list[tuple[str, int | None]] = [
                    (value.strip(), None)
                    for value in (caption, header)
                    if isinstance(value, str) and value.strip()
                ]
                if isinstance(rows, list) and all(isinstance(row, str) for row in rows):
                    parts.extend(
                        (row.strip(), index) for index, row in enumerate(rows) if row.strip()
                    )
                else:
                    parts = []
                if parts and "\n".join(value for value, _ in parts) == text:
                    cursor = 0
                    for part_index, (value, body_index) in enumerate(parts):
                        spans.append(
                            {
                                **spans[0],
                                "element_id": identity + f":part:{part_index}",
                                "table_element_id": identity,
                                "table_body_row_index": body_index,
                                "table_shared_context": body_index is None,
                                "text": value,
                                "char_start": start + cursor,
                                "char_end": start + cursor + len(value),
                            }
                        )
                        cursor += len(value) + 1
        elements.append(
            replace(
                element,
                metadata={
                    **element.metadata,
                    "source_spans": spans,
                    "table_element_id": spans[0]["element_id"]
                    if spans and element.element_type.value == "table"
                    else None,
                },
            )
        )
    return replace(parsed, elements=tuple(elements))


def annotate_structure(
    chunks: list[DraftChunk],
    parsed: ParsedDocument,
    *,
    max_tokens: int,
    token_counter: TokenCountingService,
    preserve_content: bool = False,
) -> None:
    """Derive scope only from carried, exact constituent spans; never guess origins."""
    del max_tokens, token_counter, preserve_content
    for draft in chunks:
        spans: list[dict[str, Any]] = []
        for span in draft.metadata.get("source_spans", []):
            start, end, text = span.get("char_start"), span.get("char_end"), span.get("text")
            if (
                type(start) is int
                and type(end) is int
                and 0 <= start < end <= len(parsed.text)
                and isinstance(text, str)
                and parsed.text[start:end] == text
                and text in draft.content
                and span_belongs_to_table_group(span, draft.metadata)
            ):
                spans.append({**span, "span_hash": hashlib.sha256(text.encode()).hexdigest()})
        contiguous = (
            draft.char_start is not None
            and draft.char_end is not None
            and parsed.text[draft.char_start : draft.char_end] == draft.content
            and (not draft.metadata.get("table_row_group") or bool(spans))
            and (
                not spans
                or (
                    min(v["char_start"] for v in spans) == draft.char_start
                    and max(v["char_end"] for v in spans) == draft.char_end
                )
            )
        )
        if not spans and contiguous:
            spans = [
                {
                    "element_id": f"raw:{draft.char_start}:{draft.char_end}",
                    "role": "text",
                    "text": draft.content,
                    "char_start": draft.char_start,
                    "char_end": draft.char_end,
                    "provenance": "exact_source_span",
                    "span_hash": hashlib.sha256(draft.content.encode()).hexdigest(),
                }
            ]
        draft.metadata["structure_version"] = "structure.v2"
        draft.metadata["structural_unit_id"] = hashlib.sha256(draft.content.encode()).hexdigest()
        draft.metadata["source_spans"] = spans
        facts = extract_scope_facts(spans, unit_id=draft.metadata["structural_unit_id"])
        draft.metadata["scope_fact_version"] = "scope.v2"
        draft.metadata["scope_facts"] = facts
        draft.metadata["provision_references"] = list(
            dict.fromkeys(fact["value"] for fact in facts if fact["kind"] == "provision")
        )
        periods = list(dict.fromkeys(fact["value"] for fact in facts if fact["kind"] == "period"))
        if len(periods) == 1:
            draft.metadata["applicable_period_candidate"] = periods[0]
        else:
            draft.metadata.pop("applicable_period_candidate", None)
        if not contiguous:
            draft.char_start = draft.char_end = None
        draft.metadata["provenance_precision"] = (
            "exact_source_span"
            if contiguous
            else "chunk_with_source_spans"
            if spans
            else "unattested"
        )


def span_belongs_to_table_group(span: dict[str, Any], metadata: dict[str, Any]) -> bool:
    """A split table owns its carried body row range plus explicit shared context."""
    if not metadata.get("table_row_group") or span.get("role") != "table":
        return True
    if not span.get("table_element_id") or span.get("table_element_id") != metadata.get(
        "table_element_id"
    ):
        return False
    if span.get("table_shared_context") is True:
        return True
    row, start, end = (
        span.get("table_body_row_index"),
        metadata.get("table_row_start"),
        metadata.get("table_row_end"),
    )
    return type(row) is int and type(start) is int and type(end) is int and start <= row <= end
