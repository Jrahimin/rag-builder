"""Layout analysis helpers for PyMuPDF PDF parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ImageRegion:
    """A raster region on a PDF page eligible for selective OCR."""

    bbox: tuple[float, float, float, float]
    area_ratio: float
    block_index: int


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Deterministic layout signals for a single PDF page."""

    page_width: float
    page_height: float
    text_block_count: int
    image_block_count: int
    is_complex: bool
    image_regions: tuple[ImageRegion, ...] = field(default_factory=tuple)
    text_blocks: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def page_area(page_width: float, page_height: float) -> float:
    return max(page_width * page_height, 1.0)


def bbox_area(bbox: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def image_area_ratio(
    bbox: tuple[float, float, float, float], page_width: float, page_height: float
) -> float:
    return bbox_area(bbox) / page_area(page_width, page_height)


def accept_ocr_result(
    text: str,
    confidence: float,
    *,
    min_text_chars: int,
    min_confidence: float,
) -> bool:
    """Return True when OCR output is worth indexing."""
    stripped = text.strip()
    if len(stripped) < min_text_chars:
        return False
    return confidence >= min_confidence


def is_complex_layout(
    *,
    text_block_count: int,
    image_block_count: int,
    text_blocks: list[dict[str, Any]],
    page_width: float,
) -> bool:
    """Detect magazine/brochure-style pages with interleaved text and figures."""
    if text_block_count == 0 or image_block_count == 0:
        return False
    if text_block_count >= 1 and image_block_count >= 1:
        return True
    if text_block_count >= 4 and image_block_count >= 1:
        return True
    if text_block_count >= 2 and image_block_count >= 2:
        return True
    # Multi-column heuristic: text blocks spread across left/right halves.
    if text_block_count >= 3 and page_width > 0:
        mid_x = page_width / 2
        left = 0
        right = 0
        for block in text_blocks:
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            center_x = (float(bbox[0]) + float(bbox[2])) / 2
            if center_x < mid_x:
                left += 1
            else:
                right += 1
        if left >= 1 and right >= 1:
            return True
    return False


def reading_order_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort text blocks top-to-bottom, left-to-right for designed layouts."""
    text_blocks = [block for block in blocks if block.get("type") == 0]
    return sorted(
        text_blocks,
        key=lambda block: (
            round(float(block["bbox"][1]), 1),
            round(float(block["bbox"][0]), 1),
        ),
    )


def analyze_page_blocks(
    blocks: list[dict[str, Any]],
    *,
    page_width: float,
    page_height: float,
    min_image_area_ratio: float,
) -> PageLayout:
    """Build layout metadata and large-image OCR candidates from PyMuPDF blocks."""
    text_blocks = [block for block in blocks if block.get("type") == 0]
    image_blocks = [block for block in blocks if block.get("type") == 1]

    image_regions: list[ImageRegion] = []
    for index, block in enumerate(image_blocks):
        bbox_raw = block.get("bbox")
        if not bbox_raw or len(bbox_raw) < 4:
            continue
        bbox = (
            float(bbox_raw[0]),
            float(bbox_raw[1]),
            float(bbox_raw[2]),
            float(bbox_raw[3]),
        )
        ratio = image_area_ratio(bbox, page_width, page_height)
        if ratio < min_image_area_ratio:
            continue
        image_regions.append(ImageRegion(bbox=bbox, area_ratio=ratio, block_index=index))

    complex_layout = is_complex_layout(
        text_block_count=len(text_blocks),
        image_block_count=len(image_blocks),
        text_blocks=text_blocks,
        page_width=page_width,
    )
    ordered_text = tuple(reading_order_blocks(blocks)) if complex_layout else tuple(text_blocks)

    return PageLayout(
        page_width=page_width,
        page_height=page_height,
        text_block_count=len(text_blocks),
        image_block_count=len(image_blocks),
        is_complex=complex_layout,
        image_regions=tuple(image_regions),
        text_blocks=ordered_text,
    )
