"""Conservative provider-neutral OCR layout to parsed-element conversion."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.document_parser import (
    ParsedElement,
    ParsedElementType,
)
from app.platform.providers.contracts.ocr import OcrBlock, OcrBoundingBox, OcrPageResult, OcrWord

_MIN_TABLE_ROWS = 4
_MIN_COLUMN_GAP = 0.045
_DEFAULT_ROW_TOLERANCE = 0.012


@dataclass(frozen=True, slots=True)
class _LayoutRow:
    words: tuple[OcrWord, ...]
    y_center: float
    y_min: float
    y_max: float

    @property
    def text(self) -> str:
        return normalize_for_storage(" ".join(word.text for word in self.words))

    @property
    def columns(self) -> tuple[str, ...]:
        if not self.words:
            return ()
        groups: list[list[OcrWord]] = [[self.words[0]]]
        for previous, word in zip(self.words, self.words[1:], strict=False):
            previous_box = previous.bounding_box
            word_box = word.bounding_box
            gap = (
                word_box.x_min - previous_box.x_max
                if previous_box is not None and word_box is not None
                else 0.0
            )
            if gap >= _MIN_COLUMN_GAP:
                groups.append([word])
            else:
                groups[-1].append(word)
        return tuple(
            normalize_for_storage(" ".join(word.text for word in group)) for group in groups
        )


def ocr_result_to_elements(result: OcrPageResult, *, page_number: int) -> tuple[ParsedElement, ...]:
    """Emit paragraphs and conservatively inferred table regions for one page."""
    rows = _rows_from_blocks(result.blocks)
    if not rows:
        return _fallback_elements(result, page_number=page_number)

    table_indexes = _table_row_indexes(rows)
    elements: list[ParsedElement] = []
    index = 0
    while index < len(rows):
        if index not in table_indexes:
            elements.append(_paragraph_element(rows[index], result, page_number))
            index += 1
            continue
        table_rows: list[_LayoutRow] = []
        while index < len(rows) and index in table_indexes:
            table_rows.append(rows[index])
            index += 1
        caption = elements[-1].text if elements and _is_caption_candidate(elements[-1]) else ""
        elements.append(_table_element(table_rows, result, page_number, caption=caption))
    return tuple(elements)


def _rows_from_blocks(blocks: tuple[OcrBlock, ...]) -> list[_LayoutRow]:
    words = [
        word
        for block in blocks
        for paragraph in block.paragraphs
        for word in paragraph.words
        if word.bounding_box is not None and word.text.strip()
    ]
    if not words:
        return []
    heights = [
        box.y_max - box.y_min
        for word in words
        if (box := word.bounding_box) is not None and box.y_max > box.y_min
    ]
    tolerance = max(_DEFAULT_ROW_TOLERANCE, (median(heights) * 0.65 if heights else 0.0))
    ordered = sorted(
        words,
        key=lambda word: (
            _center(word.bounding_box).y,
            _center(word.bounding_box).x,
        ),
    )
    grouped: list[list[OcrWord]] = []
    centers: list[float] = []
    for word in ordered:
        box = word.bounding_box
        if box is None:
            continue
        y_center = (box.y_min + box.y_max) / 2
        if grouped and abs(y_center - centers[-1]) <= tolerance:
            grouped[-1].append(word)
            centers[-1] = sum(
                ((item.bounding_box.y_min + item.bounding_box.y_max) / 2)
                for item in grouped[-1]
                if item.bounding_box is not None
            ) / len(grouped[-1])
        else:
            grouped.append([word])
            centers.append(y_center)
    rows: list[_LayoutRow] = []
    for group, center in zip(grouped, centers, strict=True):
        sorted_group = tuple(
            sorted(
                group,
                key=lambda word: word.bounding_box.x_min if word.bounding_box is not None else 0.0,
            )
        )
        boxes = [word.bounding_box for word in sorted_group if word.bounding_box is not None]
        rows.append(
            _LayoutRow(
                words=sorted_group,
                y_center=center,
                y_min=min(box.y_min for box in boxes),
                y_max=max(box.y_max for box in boxes),
            )
        )
    return rows


def _table_row_indexes(rows: list[_LayoutRow]) -> set[int]:
    candidates = [len(row.columns) >= 2 for row in rows]
    selected: set[int] = set()
    start = 0
    while start < len(rows):
        if not candidates[start]:
            start += 1
            continue
        end = start
        while end < len(rows) and candidates[end]:
            end += 1
        if end - start >= _MIN_TABLE_ROWS:
            selected.update(range(start, end))
        start = end
    selected.update(_caption_led_table_indexes(rows))
    return selected


def _caption_led_table_indexes(rows: list[_LayoutRow]) -> set[int]:
    """Recognize wrapped tables after a short centered caption-like row."""
    selected: set[int] = set()
    for caption_index, caption in enumerate(rows[:-5]):
        boxes = [
            word.bounding_box for word in caption.words if word.bounding_box is not None
        ]
        if not boxes or len(caption.words) > 3:
            continue
        x_min = min(box.x_min for box in boxes)
        x_max = max(box.x_max for box in boxes)
        center = (x_min + x_max) / 2
        if x_max - x_min > 0.3 or not 0.35 <= center <= 0.65:
            continue
        lookahead = rows[caption_index + 1 : caption_index + 9]
        multi_column_count = sum(len(row.columns) >= 2 for row in lookahead)
        numeric_count = sum(
            any(character.isdigit() for character in row.text) for row in lookahead
        )
        if multi_column_count < 2 or numeric_count < 2:
            continue

        start = caption_index + 1
        end = start
        while end < len(rows):
            if end > start and rows[end].y_min - rows[end - 1].y_max >= 0.011:
                break
            end += 1
        if end - start >= 5:
            selected.update(range(start, end))
    return selected


def _paragraph_element(
    row: _LayoutRow,
    result: OcrPageResult,
    page_number: int,
) -> ParsedElement:
    return ParsedElement(
        text=row.text,
        element_type=ParsedElementType.PARAGRAPH,
        page_start=page_number,
        page_end=page_number,
        metadata={
            "ocr_confidence": result.confidence,
            "ocr_source": result.provider_name,
            "content_source": "ocr_layout_row",
            "bounding_box": _row_box(row),
        },
    )


def _table_element(
    rows: list[_LayoutRow],
    result: OcrPageResult,
    page_number: int,
    *,
    caption: str,
) -> ParsedElement:
    rendered_rows = [" | ".join(row.columns) for row in rows]
    header = rendered_rows[0]
    body_rows = rendered_rows[1:]
    content = "\n".join(part for part in (caption, header, *body_rows) if part)
    return ParsedElement(
        text=content,
        element_type=ParsedElementType.TABLE,
        page_start=page_number,
        page_end=page_number,
        metadata={
            "ocr_confidence": result.confidence,
            "ocr_source": result.provider_name,
            "content_source": "ocr_layout_table",
            "layout_inference": "aligned_columns_v1",
            "table_caption": caption,
            "table_header": header,
            "table_rows": body_rows,
            "bounding_box": {
                "x_min": min(
                    word.bounding_box.x_min
                    for row in rows
                    for word in row.words
                    if word.bounding_box is not None
                ),
                "y_min": min(row.y_min for row in rows),
                "x_max": max(
                    word.bounding_box.x_max
                    for row in rows
                    for word in row.words
                    if word.bounding_box is not None
                ),
                "y_max": max(row.y_max for row in rows),
            },
        },
    )


def _fallback_elements(result: OcrPageResult, *, page_number: int) -> tuple[ParsedElement, ...]:
    parts = list(result.lines) or [result.text]
    return tuple(
        ParsedElement(
            text=normalized,
            element_type=ParsedElementType.PARAGRAPH,
            page_start=page_number,
            page_end=page_number,
            metadata={
                "ocr_confidence": result.confidence,
                "ocr_source": result.provider_name,
                "content_source": "ocr_page",
            },
        )
        for part in parts
        if (normalized := normalize_for_storage(part))
    )


def _is_caption_candidate(element: ParsedElement) -> bool:
    return element.element_type is ParsedElementType.PARAGRAPH and len(element.text.split()) <= 20


def _row_box(row: _LayoutRow) -> dict[str, float]:
    boxes = [word.bounding_box for word in row.words if word.bounding_box is not None]
    return {
        "x_min": min(box.x_min for box in boxes),
        "y_min": row.y_min,
        "x_max": max(box.x_max for box in boxes),
        "y_max": row.y_max,
    }


@dataclass(frozen=True, slots=True)
class _Center:
    x: float
    y: float


def _center(box: OcrBoundingBox | None) -> _Center:
    if box is None:
        return _Center(0.0, 0.0)
    return _Center((box.x_min + box.x_max) / 2, (box.y_min + box.y_max) / 2)
