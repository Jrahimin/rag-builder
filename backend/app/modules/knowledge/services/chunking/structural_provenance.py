"""Shared parser-attested provenance for every chunking strategy."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.platform.providers.contracts.document_parser import ParsedDocument, ParsedElementType


def annotate_structure(
    chunks: list[DraftChunk],
    parsed: ParsedDocument,
    *,
    max_tokens: int,
    token_counter: TokenCountingService,
    preserve_content: bool = False,
) -> None:
    elements = parsed.elements
    for draft in chunks:
        origin = draft.char_start
        if origin is None or parsed.text[origin : origin + len(draft.content)] != draft.content:
            found = parsed.text.find(draft.content)
            origin = (
                found if found >= 0 and parsed.text.find(draft.content, found + 1) < 0 else None
            )
        headings: list[tuple[int, str]] = []
        for element in elements:
            if origin is None or element.char_start is None or element.char_start > origin:
                continue
            if element.element_type is ParsedElementType.HEADING and element.metadata.get(
                "role"
            ) not in {"footer", "header", "contents", "boilerplate"}:
                level = element.heading_level or 1
                headings = [(depth, text) for depth, text in headings if depth < level] + [
                    (level, element.text.strip())
                ]
        texts = [t for _, t in headings][-4:]
        prefix = "\n\n".join(t for t in texts if t and t not in draft.content)
        if (
            prefix
            and not preserve_content
            and token_counter.count(prefix + "\n\n" + draft.content) <= max_tokens
        ):
            draft.content = prefix + "\n\n" + draft.content
            draft.metadata["heading_path"] = texts
            draft.metadata["heading_context_status"] = "preserved"
    for draft in chunks:
        spans: list[dict[str, Any]] = []
        for element in elements:
            if element.metadata.get("role") in {"footer", "header", "contents", "boilerplate"}:
                continue
            if element.text.strip() and element.text.strip() in draft.content:
                start, end = element.char_start, element.char_end
                exact = (
                    start is not None
                    and end is not None
                    and parsed.text[start:end].strip() == element.text.strip()
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
        if not spans:
            position = parsed.text.find(draft.content)
            if position >= 0 and parsed.text.find(draft.content, position + 1) < 0:
                spans.append(
                    {
                        "role": "text",
                        "text": draft.content,
                        "char_start": position,
                        "char_end": position + len(draft.content),
                        "provenance": "exact_source_span",
                    }
                )
        draft.metadata["structure_version"] = "structure.v1"
        draft.metadata["structural_unit_id"] = hashlib.sha256(draft.content.encode()).hexdigest()
        draft.metadata["source_spans"] = spans
        scope_facts: list[dict[str, Any]] = []
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
        for span in spans:
            text = str(span["text"]).translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
            for match in re.finditer(
                r"(?P<label>assessment\s+year|tax\s+year|fiscal\s+year|financial\s+year|"
                r"calendar\s+year|AY|FY|করবর্ষ|অর্থবছর)\s*[: -]?\s*(?P<start>20\d{2})"
                r"(?:\s*[-\u2013/]\s*(?P<end>20\d{2}|\d{2}))?",
                text,
                re.I,
            ):
                start = int(match["start"])
                end = int(match["end"]) if match["end"] else start
                if end < 100:
                    end += start // 100 * 100
                    if end < start:
                        end += 100
                label = match["label"].casefold()
                kind = (
                    "assessment"
                    if label in {"ay", "করবর্ষ", "assessment year", "tax year"}
                    else "fiscal"
                    if label in {"fy", "অর্থবছর", "fiscal year", "financial year"}
                    else "calendar"
                )
                scope_facts.append(
                    {
                        "kind": "period",
                        "legal_kind": kind,
                        "start_year": start,
                        "end_year": end,
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
            or parsed.text[draft.char_start : draft.char_end] != draft.content
        ):
            draft.char_start = draft.char_end = None
            draft.metadata["provenance_precision"] = "chunk_with_source_spans"
        else:
            draft.metadata["provenance_precision"] = "exact_source_span"
