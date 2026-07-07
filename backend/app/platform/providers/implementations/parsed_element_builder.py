"""Shared helpers for building parsed document elements."""

from __future__ import annotations

from app.platform.providers.contracts.document_parser import (
    ParsedElement,
    ParsedElementType,
    build_plain_text_projection,
)


def assign_char_offsets(elements: list[ParsedElement]) -> list[ParsedElement]:
    """Compute char_start/char_end for elements based on plain-text projection."""
    if not elements:
        return []

    text_parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    cursor = 0

    for index, element in enumerate(elements):
        stripped = element.text.strip()
        if not stripped:
            offsets.append((None, None))  # type: ignore[list-item]
            continue
        if text_parts:
            cursor += 2
        start = cursor
        cursor += len(stripped)
        text_parts.append(stripped)
        offsets.append((start, cursor))

    updated: list[ParsedElement] = []
    for element, (start, end) in zip(elements, offsets, strict=True):
        updated.append(
            ParsedElement(
                text=element.text,
                element_type=element.element_type,
                page_start=element.page_start,
                page_end=element.page_end,
                heading_level=element.heading_level,
                char_start=start,
                char_end=end,
                metadata=element.metadata,
            )
        )
    return updated


def finalize_elements(elements: list[ParsedElement]) -> tuple[str, tuple[ParsedElement, ...]]:
    """Assign offsets and return plain-text projection plus element tuple."""
    with_offsets = assign_char_offsets(elements)
    text = build_plain_text_projection(with_offsets)
    return text, tuple(with_offsets)
