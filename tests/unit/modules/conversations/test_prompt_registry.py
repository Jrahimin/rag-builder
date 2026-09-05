"""Unit tests for the canonical grounding prompt."""

from __future__ import annotations

import pytest

from app.modules.conversations.prompts.registry import (
    GROUNDED_PROMPT_VERSION,
    has_prompt_template,
    require_prompt_template,
)

pytestmark = pytest.mark.unit


def test_canonical_prompt_is_returned_for_any_version() -> None:
    assert has_prompt_template("v1") is True
    assert has_prompt_template("missing") is True
    template = require_prompt_template("v1")
    assert template.version == GROUNDED_PROMPT_VERSION
    assert "yes/no" in template.template
    assert "compute the result" in template.template
    assert "If knowledge and web evidence conflict" in template.template
    assert "scenario assumptions" in template.template
    assert "Adopted values are scenario inputs" in template.template
    assert require_prompt_template("v9") is template
