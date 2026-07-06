"""Unit tests for validation error field path formatting."""

from __future__ import annotations

from app.core.exception_handlers import _validation_field_path


def test_validation_field_path_missing_entire_body() -> None:
    assert _validation_field_path(("body",)) == "body"


def test_validation_field_path_missing_body_field() -> None:
    assert _validation_field_path(("body", "query")) == "query"


def test_validation_field_path_nested_body_field() -> None:
    assert _validation_field_path(("body", "metadata_filter", "source")) == "metadata_filter.source"


def test_validation_field_path_query_param() -> None:
    assert _validation_field_path(("query", "limit")) == "query.limit"
