"""Shared helpers for structure-aware chunk strategies."""

from __future__ import annotations

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

    for element in elements:
        if element.element_type is ParsedElementType.PAGE_BREAK:
            continue
        if element.element_type is ParsedElementType.HEADING:
            current_section_title = element.text.strip()

        draft = element_to_draft(element, section_title=current_section_title)
        draft.metadata["strategy_used"] = strategy_name
        token_count = token_counter.count(draft.content)

        if element.element_type is ParsedElementType.TABLE:
            if buffer is not None:
                chunks.append(buffer)
                buffer = None
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
            continue

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
        for value in (caption, header)
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
    sections = group_sections(list(context.parsed.elements))
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
                    metadata={"strategy_used": strategy_name, "section_chunk": True},
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
