"""LanguageScope never hides mixed or unknown rows."""

from __future__ import annotations

import pytest

from app.modules.retrieval.language_scope import LanguageScope, language_scope_predicate
from app.platform.domain.language_detection import ROUTING_LANGUAGE_MIXED, ROUTING_LANGUAGE_UNKNOWN

pytestmark = pytest.mark.unit


def test_translated_scope_includes_mixed_and_unknown() -> None:
    scope = LanguageScope.translated_target("bn")
    assert "bn" in scope.include_languages
    assert scope.include_mixed is True
    assert scope.include_unknown is True


def test_unfiltered_scope_has_no_predicate() -> None:
    from sqlalchemy import column

    assert language_scope_predicate(column("chunk_language"), None) is None


def test_translated_predicate_keeps_mixed_unknown_and_missing() -> None:
    from sqlalchemy import column

    predicate = language_scope_predicate(
        column("chunk_language"),
        LanguageScope.translated_target("bn"),
    )
    assert predicate is not None
    compiled = predicate.compile()
    bound = " ".join(str(value) for value in compiled.params.values())
    assert "bn" in bound
    assert ROUTING_LANGUAGE_MIXED in bound
    assert ROUTING_LANGUAGE_UNKNOWN in bound
    assert "IS NULL" in str(compiled)


def test_scope_constants_match_routing_codes() -> None:
    assert ROUTING_LANGUAGE_MIXED == "mixed"
    assert ROUTING_LANGUAGE_UNKNOWN == "unknown"
