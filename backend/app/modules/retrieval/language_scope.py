"""Conservative language filters for translated retrieval branches."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement, or_

from app.platform.domain.language_detection import (
    ROUTING_LANGUAGE_MIXED,
    ROUTING_LANGUAGE_UNKNOWN,
)


@dataclass(frozen=True, slots=True)
class LanguageScope:
    """Include exact target languages plus mixed/unknown rows that must never be hidden."""

    include_languages: tuple[str, ...]
    include_mixed: bool = True
    include_unknown: bool = True

    @classmethod
    def translated_target(cls, target_language: str) -> LanguageScope:
        return cls(include_languages=(target_language,), include_mixed=True, include_unknown=True)


def language_scope_predicate(
    language_column: ColumnElement[str],
    scope: LanguageScope | None,
) -> ColumnElement[bool] | None:
    """SQL predicate that never hides mixed or unknown/missing language rows."""
    if scope is None:
        return None
    allowed = list(scope.include_languages)
    if scope.include_mixed:
        allowed.append(ROUTING_LANGUAGE_MIXED)
    if scope.include_unknown:
        allowed.append(ROUTING_LANGUAGE_UNKNOWN)
    conditions: list[ColumnElement[bool]] = [language_column.in_(allowed)]
    if scope.include_unknown:
        conditions.append(language_column.is_(None))
        conditions.append(language_column == "")
    return or_(*conditions)
