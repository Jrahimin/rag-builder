"""Unit tests for prompt registry."""

from __future__ import annotations

import pytest

from app.core.exceptions import BadRequestError
from app.modules.conversations.prompts.registry import has_prompt_template, require_prompt_template

pytestmark = pytest.mark.unit


def test_has_prompt_template() -> None:
    assert has_prompt_template("v1") is True
    assert has_prompt_template("missing") is False


def test_require_prompt_template_raises_bad_request() -> None:
    with pytest.raises(BadRequestError, match="Unknown system prompt version"):
        require_prompt_template("missing")
