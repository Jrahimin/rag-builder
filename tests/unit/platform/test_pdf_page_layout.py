"""Unit tests for PDF layout analysis helpers."""

from __future__ import annotations

import pytest

from app.platform.providers.implementations.pdf_page_layout import (
    accept_ocr_result,
    analyze_page_blocks,
    image_area_ratio,
    is_complex_layout,
    reading_order_blocks,
)

pytestmark = pytest.mark.unit


def test_accept_ocr_result_requires_min_chars_and_confidence() -> None:
    assert accept_ocr_result("short", 0.9, min_text_chars=20, min_confidence=0.3) is False
    assert accept_ocr_result("x" * 25, 0.9, min_text_chars=20, min_confidence=0.3) is True
    assert accept_ocr_result("x" * 25, 0.2, min_text_chars=20, min_confidence=0.3) is False


def test_image_area_ratio_filters_small_logos() -> None:
    page_w, page_h = 600.0, 800.0
    logo = (10.0, 10.0, 60.0, 60.0)
    figure = (50.0, 200.0, 550.0, 600.0)
    assert image_area_ratio(logo, page_w, page_h) < 0.08
    assert image_area_ratio(figure, page_w, page_h) > 0.08


def test_is_complex_layout_detects_mixed_pages() -> None:
    assert is_complex_layout(
        text_block_count=5,
        image_block_count=2,
        text_blocks=[],
        page_width=600,
    )
    assert not is_complex_layout(
        text_block_count=1,
        image_block_count=0,
        text_blocks=[],
        page_width=600,
    )


def test_reading_order_sorts_top_to_bottom() -> None:
    blocks = [
        {"type": 0, "bbox": [0, 200, 100, 220]},
        {"type": 0, "bbox": [0, 10, 100, 30]},
        {"type": 1, "bbox": [300, 10, 500, 200]},
    ]
    ordered = reading_order_blocks(blocks)
    assert ordered[0]["bbox"][1] < ordered[1]["bbox"][1]


def test_analyze_page_blocks_skips_small_images() -> None:
    blocks = [
        {"type": 0, "bbox": [0, 0, 500, 100], "lines": []},
        {"type": 1, "bbox": [10, 10, 40, 40]},
        {"type": 1, "bbox": [50, 150, 550, 650]},
    ]
    layout = analyze_page_blocks(
        blocks,
        page_width=600,
        page_height=800,
        min_image_area_ratio=0.08,
    )
    assert layout.is_complex is True
    assert len(layout.image_regions) == 1
    assert layout.image_regions[0].area_ratio > 0.08
