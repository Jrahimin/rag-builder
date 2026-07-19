"""Doctor output classification without external provider calls."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cli.doctor_cli import _run_check

pytestmark = pytest.mark.unit


async def test_doctor_check_returns_non_secret_failure() -> None:
    operation = MagicMock(return_value=AsyncMock(side_effect=RuntimeError("password=secret"))())

    result = await _run_check("redis", operation, action="Verify Redis.")

    assert result.state == "FAIL"
    assert "secret" not in result.detail
    assert result.critical is True
