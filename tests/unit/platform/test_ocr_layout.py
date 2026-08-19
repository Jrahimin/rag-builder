"""OCR geometry reconstruction tests."""

from __future__ import annotations

import pytest

from app.platform.providers.contracts.document_parser import ParsedElementType
from app.platform.providers.contracts.ocr import (
    OcrBlock,
    OcrBoundingBox,
    OcrPageResult,
    OcrParagraph,
    OcrWord,
)
from app.platform.providers.implementations.ocr_layout import ocr_result_to_elements

pytestmark = pytest.mark.unit


def _word(text: str, x_min: float, y_min: float, x_max: float, y_max: float) -> OcrWord:
    return OcrWord(
        text=text,
        bounding_box=OcrBoundingBox(x_min, y_min, x_max, y_max),
        confidence=0.9,
    )


def test_layout_infers_only_consecutive_aligned_rows_as_table() -> None:
    words = (
        _word("Source", 0.1, 0.10, 0.2, 0.12),
        _word("heads", 0.21, 0.10, 0.3, 0.12),
        _word("Category", 0.1, 0.20, 0.25, 0.22),
        _word("Rate", 0.7, 0.20, 0.8, 0.22),
        _word("Savings", 0.1, 0.25, 0.25, 0.27),
        _word("10", 0.7, 0.25, 0.75, 0.27),
        _word("Property", 0.1, 0.30, 0.25, 0.32),
        _word("5", 0.7, 0.30, 0.75, 0.32),
        _word("Export", 0.1, 0.35, 0.25, 0.37),
        _word("2", 0.7, 0.35, 0.75, 0.37),
    )
    result = OcrPageResult(
        text="Source heads Category Rate Savings 10 Property 5",
        confidence=0.9,
        provider_name="test",
        blocks=(
            OcrBlock(
                text="table",
                paragraphs=(OcrParagraph(text="table", words=words),),
            ),
        ),
        page_number=1,
    )

    elements = ocr_result_to_elements(result, page_number=1)

    assert [element.element_type for element in elements] == [
        ParsedElementType.PARAGRAPH,
        ParsedElementType.TABLE,
    ]
    table = elements[1]
    assert table.metadata["table_caption"] == "Source heads"
    assert table.metadata["table_header"] == "Category | Rate"
    assert table.metadata["table_rows"] == ["Savings | 10", "Property | 5", "Export | 2"]
    assert table.page_start == 1


def test_layout_recognizes_wrapped_table_after_centered_short_caption() -> None:
    words = (
        _word("Table", 0.47, 0.10, 0.53, 0.12),
        _word("Category", 0.1, 0.14, 0.25, 0.16),
        _word("Person", 0.7, 0.14, 0.8, 0.16),
        _word("1", 0.1, 0.165, 0.12, 0.18),
        _word("Savings", 0.2, 0.165, 0.35, 0.18),
        _word("105", 0.7, 0.165, 0.75, 0.18),
        _word("continued", 0.2, 0.185, 0.35, 0.20),
        _word("2", 0.1, 0.205, 0.12, 0.22),
        _word("Property", 0.2, 0.205, 0.35, 0.22),
        _word("125", 0.7, 0.205, 0.75, 0.22),
        _word("continued", 0.2, 0.225, 0.35, 0.24),
        _word("prose", 0.1, 0.26, 0.2, 0.28),
    )
    result = OcrPageResult(
        text="wrapped table",
        confidence=0.9,
        provider_name="test",
        blocks=(
            OcrBlock(
                text="wrapped table",
                paragraphs=(OcrParagraph(text="wrapped table", words=words),),
            ),
        ),
    )

    elements = ocr_result_to_elements(result, page_number=3)

    assert any(element.element_type is ParsedElementType.TABLE for element in elements)
    table = next(element for element in elements if element.element_type is ParsedElementType.TABLE)
    assert table.metadata["table_caption"] == "Table"
    assert "Property" in table.text


def test_layout_falls_back_to_paragraphs_without_geometry() -> None:
    result = OcrPageResult(
        text="Category Rate\nSavings 10",
        confidence=0.9,
        provider_name="text_only",
        lines=("Category Rate", "Savings 10"),
        blocks=(),
        page_number=2,
    )

    elements = ocr_result_to_elements(result, page_number=2)

    assert [element.element_type for element in elements] == [
        ParsedElementType.PARAGRAPH,
        ParsedElementType.PARAGRAPH,
    ]
    assert [element.text for element in elements] == ["Category Rate", "Savings 10"]
    assert all(element.page_start == 2 and element.page_end == 2 for element in elements)
