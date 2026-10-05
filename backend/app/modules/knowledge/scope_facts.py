"""Versioned source scope: extracted mentions never authorize hard exclusion."""

from __future__ import annotations

import re
from typing import Any, Literal

from app.platform.domain.source_scope import ScopeFact
from app.platform.domain.source_scope import span_hash as span_hash

SCOPE_FACT_VERSION: Literal["scope.v2"] = "scope.v2"
_DIGITS = str.maketrans(
    "\u09e6\u09e7\u09e8\u09e9\u09ea\u09eb\u09ec\u09ed\u09ee\u09ef", "0123456789"
)
_LABEL = re.compile(
    r"assessment\s+years?|tax\s+years?|fiscal\s+years?|financial\s+years?|"
    r"calendar\s+years?|AY|FY|করবর্ষ|অর্থবছর",
    re.I,
)
_PERIOD = re.compile(r"(?<!\d)(20\d{2})(?:\s*[-\u2013/]\s*(20\d{2}|\d{2}))?(?!\d)")


def extract_scope_facts(spans: list[dict[str, Any]], *, unit_id: str) -> list[dict[str, Any]]:
    """Parse plural year lists within source spans; infer neither effect nor exhaustive scope."""
    facts: list[dict[str, Any]] = []
    for span in spans:
        text = str(span.get("text", "")).translate(_DIGITS)
        locality: Literal["table", "span"] = (
            "table" if span.get("role") in {"table", "table_row", "table_cell"} else "span"
        )
        effect: Literal["unknown", "proposal", "example"] = (
            "proposal"
            if re.search(r"\bpropos(?:al|ed|e)\b|প্রস্তাব", text, re.I)
            else "example"
            if re.search(r"\bexample\b|উদাহরণ", text, re.I)
            else "unknown"
        )
        labels = list(_LABEL.finditer(text))
        for match in _PERIOD.finditer(text):
            previous = [label for label in labels if label.end() <= match.start()]
            label = previous[-1] if previous else next(iter(labels), None)
            # A label can govern a local table or comma-separated year list, never another span.
            legal_kind: Literal["assessment", "fiscal", "calendar"] | None = None
            if label is not None and (
                locality == "table" or abs(label.end() - match.start()) <= 180
            ):
                normalized = label.group().lower()
                legal_kind = (
                    "assessment"
                    if normalized.startswith(("assessment", "tax")) or normalized in {"ay", "করবর্ষ"}
                    else "fiscal"
                    if normalized.startswith(("fiscal", "financial"))
                    or normalized in {"fy", "অর্থবছর"}
                    else "calendar"
                )
            start = int(match[1])
            end = int(match[2]) if match[2] else start
            if end < 100:
                end += start // 100 * 100
                if end < start:
                    end += 100
            facts.append(
                ScopeFact(
                    kind="period",
                    value=match.group(),
                    legal_kind=legal_kind,
                    start_year=start,
                    end_year=end,
                    locality=locality,
                    locality_id=unit_id,
                    effect=effect,
                    source_span=span,
                ).model_dump(mode="json")
            )
        for match in re.finditer(
            r"(?:section|article|rule|regulation|ধারা|বিধি)\s+\d+(?:[A-Za-z()./-][\dA-Za-z()./-]*)?",
            text,
            re.I,
        ):
            facts.append(
                ScopeFact(
                    kind="provision",
                    value=match.group(),
                    locality="provision",
                    locality_id=match.group().lower(),
                    effect=effect,
                    source_span=span,
                ).model_dump(mode="json")
            )
    return facts


def affirmatively_incompatible(
    facts: list[dict[str, Any]], requested: list[dict[str, Any]]
) -> bool:
    """Only reviewed exhaustive governing DOCUMENT scope can remove a document."""
    valid: list[ScopeFact] = []
    for raw in facts:
        try:
            fact = ScopeFact.model_validate(raw)
        except ValueError:
            continue
        if (
            fact.kind == "period"
            and fact.scope == "governing"
            and fact.locality == "document"
            and fact.exhaustive
            and fact.effect == "operative"
        ):
            valid.append(fact)
    if not requested or not valid:
        return False
    for period in requested:
        comparable = [f for f in valid if f.legal_kind == period.get("kind")]
        if not comparable:
            return False
        if any(
            (f.start_year == period.get("start_year") and f.end_year == period.get("end_year"))
            or (
                f.period_mode == "range"
                and f.start_year is not None
                and f.end_year is not None
                and f.start_year <= period["start_year"]
                and f.end_year >= period["end_year"]
            )
            for f in comparable
        ):
            return False
    return True
