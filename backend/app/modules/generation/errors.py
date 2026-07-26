"""Stable contextual generation errors."""

from __future__ import annotations

from app.core.exceptions import APEError


class GenerationOutputValidationError(APEError):
    """The provider returned output that did not satisfy the resolved schema."""

    status_code = 502
    code = "generation_output_schema_mismatch"
    message = "The language model returned output that did not match the response schema."
