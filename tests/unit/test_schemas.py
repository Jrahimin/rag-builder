"""Unit tests for the standard API response envelopes."""

from __future__ import annotations

import pytest

from app.schemas.common import ApiResponse, ErrorInfo, ErrorResponse, ResponseMeta

pytestmark = pytest.mark.unit


def test_api_response_ok_wraps_data() -> None:
    response = ApiResponse.ok({"value": 1}, message="done")
    assert response.success is True
    assert response.data == {"value": 1}
    assert response.message == "done"


def test_api_response_ok_with_meta() -> None:
    meta = ResponseMeta(request_id="req-1", trace_id="trace-1")
    response = ApiResponse.ok(None, meta=meta)
    assert response.meta is not None
    assert response.meta.trace_id == "trace-1"


def test_error_response_shape() -> None:
    err = ErrorResponse(error=ErrorInfo(code="not_found", message="missing"))
    dumped = err.model_dump(exclude_none=True)
    assert dumped["success"] is False
    assert dumped["error"]["code"] == "not_found"
    assert "details" not in dumped["error"]
