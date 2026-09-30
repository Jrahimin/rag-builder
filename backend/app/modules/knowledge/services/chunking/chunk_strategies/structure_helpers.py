"""Shared helpers for structure-aware chunk strategies."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from app.modules.knowledge.services.chunking.models import ChunkingContext, DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.platform.providers.contracts.document_parser import ParsedElement, ParsedElementType

from .recursive_fallback_chunk_strategy import RecursiveFallbackChunkStrategy


def group_sections(elements: list[ParsedElement]) -> list[list[ParsedElement]]:
    """Group elements into sections separated by headings."""
    sections: list[list[ParsedElement]] = []
    current: list[ParsedElement] = []
    for element in elements:
        if element.element_type is ParsedElementType.PAGE_BREAK:
            continue
        if element.element_type is ParsedElementType.HEADING and current:
            sections.append(current)
            current = [element]
            continue
        current.append(element)
    if current:
        sections.append(current)
    return sections if sections else [elements]


def element_to_draft(element: ParsedElement, *, section_title: str | None) -> DraftChunk:
    return DraftChunk(
        content=element.text.strip(),
        char_start=element.char_start,
        char_end=element.char_end,
        page_start=element.page_start,
        page_end=element.page_end,
        section_title=section_title,
        heading_level=element.heading_level,
        metadata={**element.metadata, "element_type": element.element_type.value},
    )


def pack_elements(
    elements: list[ParsedElement],
    *,
    context: ChunkingContext,
    token_counter: TokenCountingService,
    fallback: RecursiveFallbackChunkStrategy,
    strategy_name: str,
) -> list[DraftChunk]:
    chunks: list[DraftChunk] = []
    current_section_title: str | None = None
    buffer: DraftChunk | None = None
    preceding: list[ParsedElement] = []

    for element in elements:
        if element.element_type is ParsedElementType.PAGE_BREAK:
            continue
        if element.element_type is ParsedElementType.HEADING:
            current_section_title = element.text.strip()
            preceding = []

        if element.metadata.get("boilerplate") or element.metadata.get("role") in {
            "footer",
            "header",
            "contents",
        }:
            continue

        draft = element_to_draft(element, section_title=current_section_title)
        draft.metadata["strategy_used"] = strategy_name
        token_count = token_counter.count(draft.content)

        if element.element_type is ParsedElementType.TABLE:
            if buffer is not None:
                chunks.append(buffer)
                buffer = None
            # A caption extracted from the last line (e.g. "rates are below")
            # is not the scope of a table. Carry the preceding source paragraph
            # and heading into indexed content, so retrieval and generation see
            # the same context. Never synthesize applicability from a filename.
            context_parts = list(
                dict.fromkeys(
                    part
                    for part in (
                        *(element.metadata.get("heading_path") or [current_section_title]),
                        *(item.text.strip() for item in preceding),
                    )
                    if part and part not in draft.content
                )
            )
            table_context = "\n\n".join(context_parts)
            if (
                table_context
                and token_counter.count(table_context) <= context.config.max_tokens // 2
            ):
                draft.metadata["table_context"] = table_context
                draft.metadata["table_context_status"] = "preserved"
                draft.content = f"{table_context}\n\n{draft.content}"
                draft.char_start = preceding[0].char_start if preceding else draft.char_start
                draft.page_start = preceding[0].page_start if preceding else draft.page_start
                for key, attribute in (
                    ("heading_context_char_start", "char_start"),
                    ("heading_context_page_start", "page_start"),
                ):
                    origin = element.metadata.get(key)
                    current = getattr(draft, attribute)
                    if isinstance(origin, int):
                        setattr(
                            draft,
                            attribute,
                            min(origin, current) if current is not None else origin,
                        )
            else:
                draft.metadata["table_context_status"] = (
                    "context_exceeds_budget" if table_context else "not_available"
                )
            token_count = token_counter.count(draft.content)
            if token_count > context.config.max_tokens:
                chunks.extend(
                    _split_table_rows(
                        draft,
                        context=context,
                        token_counter=token_counter,
                        fallback=fallback,
                    )
                )
            else:
                chunks.append(draft)
            preceding = []
            continue

        # OCR layout paragraphs are often single lines. Retain a bounded run,
        # including across page breaks, so a footer cannot replace legal scope.
        preceding.append(element)
        while len(preceding) > 1 and (
            len(preceding) > 4
            or token_counter.count("\n\n".join(item.text for item in preceding))
            > context.config.max_tokens // 2
        ):
            preceding.pop(0)

        if buffer is None:
            if token_count > context.config.max_tokens:
                chunks.extend(
                    fallback.split_text(
                        draft.content,
                        config=context.config,
                        base_metadata=draft.metadata,
                        page_start=draft.page_start,
                        page_end=draft.page_end,
                        section_title=draft.section_title,
                        heading_level=draft.heading_level,
                    )
                )
            else:
                buffer = draft
            continue

        combined = _merge_drafts(buffer, draft)
        if token_counter.count(combined.content) <= context.config.target_tokens:
            buffer = combined
            continue

        chunks.append(buffer)
        if token_count > context.config.max_tokens:
            chunks.extend(
                fallback.split_text(
                    draft.content,
                    config=context.config,
                    base_metadata=draft.metadata,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    section_title=draft.section_title,
                    heading_level=draft.heading_level,
                )
            )
            buffer = None
        else:
            buffer = draft

    if buffer is not None:
        chunks.append(buffer)
    return chunks


def _split_table_rows(
    draft: DraftChunk,
    *,
    context: ChunkingContext,
    token_counter: TokenCountingService,
    fallback: RecursiveFallbackChunkStrategy,
) -> list[DraftChunk]:
    caption = draft.metadata.get("table_caption")
    header = draft.metadata.get("table_header")
    rows = draft.metadata.get("table_rows")
    if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
        return fallback.split_text(
            draft.content,
            config=context.config,
            base_metadata=draft.metadata,
            page_start=draft.page_start,
            page_end=draft.page_end,
            section_title=draft.section_title,
            heading_level=draft.heading_level,
        )
    prefix = "\n".join(
        value.strip()
        for value in (draft.metadata.get("table_context"), caption, header)
        if isinstance(value, str) and value.strip()
    )
    groups: list[list[str]] = []
    current: list[str] = []
    for row in rows:
        candidate_rows = [*current, row]
        candidate = "\n".join(part for part in (prefix, *candidate_rows) if part)
        if current and token_counter.count(candidate) > context.config.max_tokens:
            groups.append(current)
            current = [row]
        else:
            current = candidate_rows
    if current:
        groups.append(current)

    chunks: list[DraftChunk] = []
    row_offset = 0
    for group in groups:
        content = "\n".join(part for part in (prefix, *group) if part)
        metadata = {
            **draft.metadata,
            "table_row_start": row_offset,
            "table_row_end": row_offset + len(group) - 1,
            "table_row_group": True,
        }
        if token_counter.count(content) > context.config.max_tokens:
            # This row cannot fit with its scope. Preserve the failure marker
            # through fallback splits; those fragments cannot prove applicability.
            metadata["table_context_status"] = "context_exceeds_budget"
            chunks.extend(
                fallback.split_text(
                    content,
                    config=context.config,
                    base_metadata=metadata,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    section_title=draft.section_title,
                    heading_level=draft.heading_level,
                )
            )
        else:
            chunks.append(
                DraftChunk(
                    content=content,
                    char_start=draft.char_start,
                    char_end=draft.char_end,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    section_title=draft.section_title,
                    heading_level=draft.heading_level,
                    metadata=metadata,
                )
            )
        row_offset += len(group)
    return chunks


def chunk_by_sections(
    context: ChunkingContext,
    *,
    token_counter: TokenCountingService,
    fallback: RecursiveFallbackChunkStrategy,
    strategy_name: str,
) -> list[DraftChunk]:
    # A subsection heading does not replace its parent's period/category scope.
    elements: list[ParsedElement] = []
    heading_path: list[tuple[int, ParsedElement]] = []
    for element in context.parsed.elements:
        if element.element_type is ParsedElementType.HEADING:
            level = element.heading_level or 1
            while heading_path and heading_path[-1][0] >= level:
                heading_path.pop()
            heading_path.append((level, element))
        elements.append(
            replace(
                element,
                metadata={
                    **element.metadata,
                    "heading_path": [heading.text.strip() for _, heading in heading_path],
                    "heading_context_char_start": heading_path[0][1].char_start
                    if heading_path
                    else None,
                    "heading_context_page_start": heading_path[0][1].page_start
                    if heading_path
                    else None,
                },
            )
        )
    sections = group_sections(elements)
    chunks: list[DraftChunk] = []
    for section in sections:
        section_tokens = sum(token_counter.count(element.text) for element in section)
        has_table = any(element.element_type is ParsedElementType.TABLE for element in section)
        if section_tokens <= context.config.max_tokens and not has_table:
            section_title = _section_title(section)
            combined_text = "\n\n".join(
                element.text.strip() for element in section if element.text.strip()
            )
            if not combined_text:
                continue
            first = section[0]
            last = section[-1]
            chunks.append(
                DraftChunk(
                    content=combined_text,
                    char_start=first.char_start,
                    char_end=last.char_end,
                    page_start=first.page_start,
                    page_end=last.page_end,
                    section_title=section_title,
                    heading_level=first.heading_level
                    if first.element_type is ParsedElementType.HEADING
                    else None,
                    metadata={
                        **first.metadata,
                        "strategy_used": strategy_name,
                        "section_chunk": True,
                    },
                )
            )
            continue
        chunks.extend(
            pack_elements(
                section,
                context=context,
                token_counter=token_counter,
                fallback=fallback,
                strategy_name=strategy_name,
            )
        )
    for draft in chunks:
        # A continuation paragraph needs its preceding governing headings just as
        # a table does. Use parser source text only; never infer periods from titles.
        headings = draft.metadata.get("heading_path") or []
        prefix = "\n\n".join(
            dict.fromkeys(
                text
                for text in headings
                if isinstance(text, str) and text and text not in draft.content
            )
        )
        if not prefix:
            continue
        content = f"{prefix}\n\n{draft.content}"
        if token_counter.count(content) > context.config.max_tokens:
            draft.metadata["heading_context_status"] = "context_exceeds_budget"
            continue
        draft.content = content
        draft.metadata["heading_context_status"] = "preserved"
        for key, attribute in (
            ("heading_context_char_start", "char_start"),
            ("heading_context_page_start", "page_start"),
        ):
            origin = draft.metadata.get(key)
            current = getattr(draft, attribute)
            if isinstance(origin, int):
                setattr(draft, attribute, min(origin, current) if current is not None else origin)
    for draft in chunks:
        spans = []
        for element in elements:
            if element.text.strip() and element.text.strip() in draft.content:
                start, end = element.char_start, element.char_end
                exact = (
                    start is not None
                    and end is not None
                    and context.parsed.text[start:end].strip() == element.text.strip()
                )
                spans.append(
                    {
                        "role": element.element_type.value,
                        "text": element.text.strip(),
                        "char_start": start if exact else None,
                        "char_end": end if exact else None,
                        "provenance": "exact_source_span" if exact else "chunk",
                    }
                )
        draft.metadata["structure_version"] = "structure.v1"
        draft.metadata["structural_unit_id"] = hashlib.sha256(draft.content.encode()).hexdigest()
        draft.metadata["source_spans"] = spans
        scope_facts = []
        for span in spans:
            for match in re.finditer(
                r"(?<![\d])(?:20[\d\u09e6-\u09ef]{2}|২\u09e6[\u09e6-\u09ef]{2})\s*[-\u2013/]\s*(?:20[\d\u09e6-\u09ef]{2}|২\u09e6[\u09e6-\u09ef]{2}|[\d\u09e6-\u09ef]{2})(?![\d])",
                str(span["text"]),
            ):
                scope_facts.append(
                    {
                        "kind": "period",
                        "value": match.group(),
                        "source_span": span,
                        "status": "source_attested",
                    }
                )
            for match in re.finditer(
                r"(?:section|article|rule|regulation|ধারা|বিধি)\s+[\d\u09e6-\u09ef]+(?:[A-Za-z()./-][\dA-Za-z()./-]*)?",
                str(span["text"]),
                re.I,
            ):
                scope_facts.append(
                    {
                        "kind": "provision",
                        "value": match.group(),
                        "source_span": span,
                        "status": "source_attested",
                    }
                )
        draft.metadata["scope_facts"] = scope_facts
        draft.metadata["provision_references"] = list(
            dict.fromkeys(fact["value"] for fact in scope_facts if fact["kind"] == "provision")
        )
        periods = list(
            dict.fromkeys(fact["value"] for fact in scope_facts if fact["kind"] == "period")
        )
        if len(periods) == 1:
            draft.metadata["applicable_period_candidate"] = periods[0]
        # Reassembled table prefixes do not have a contiguous document offset.
        if (
            draft.char_start is None
            or draft.char_end is None
            or context.parsed.text[draft.char_start : draft.char_end] != draft.content
        ):
            draft.char_start = draft.char_end = None
            draft.metadata["provenance_precision"] = "chunk_with_source_spans"
    return chunks


def _section_title(section: list[ParsedElement]) -> str | None:
    for element in section:
        if element.element_type is ParsedElementType.HEADING:
            return element.text.strip()
    return None


def _merge_drafts(left: DraftChunk, right: DraftChunk) -> DraftChunk:
    return DraftChunk(
        content=f"{left.content.strip()}\n\n{right.content.strip()}".strip(),
        char_start=left.char_start,
        char_end=right.char_end,
        page_start=left.page_start or right.page_start,
        page_end=right.page_end or left.page_end,
        section_title=left.section_title or right.section_title,
        heading_level=left.heading_level or right.heading_level,
        metadata={**left.metadata, **right.metadata},
    )
