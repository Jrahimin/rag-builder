"""Pagination contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.platform.http.pagination import ListParams, OperatorListParams

pytestmark = pytest.mark.unit


def test_list_params_reject_pages_larger_than_one_hundred() -> None:
    with pytest.raises(ValidationError):
        ListParams(limit=500)


def test_operator_list_params_accept_admin_bulk_page_size() -> None:
    params = OperatorListParams(limit=500, include_deleted=True)
    assert params.limit == 500
    assert params.include_deleted is True
