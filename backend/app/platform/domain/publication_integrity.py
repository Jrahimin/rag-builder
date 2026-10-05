"""Conservative text integrity signals, independent of prose/table formatting."""

from __future__ import annotations

import re

from app.platform.domain.parse_quality import ParseQualityScorer


def damaged_source_text(text: str) -> bool:
    """Detect corruption and incomplete conditions without scoring table syntax."""
    signals = ParseQualityScorer().assess(text).signals
    return any(
        value > 0
        for value in (
            signals.control_ratio,
            signals.replacement_ratio,
            signals.private_use_ratio,
            signals.surrogate_ratio,
        )
    ) or any(
        text.count(left) != text.count(right) for left, right in [("(", ")"), ("\uff08", "\uff09")]
    )


def complete_publication_unit(
    text: str, *, structured: bool = False, semantic_complete: bool | None = None
) -> bool:
    """A source match alone cannot certify a cut-off assertion in any language.

    Punctuation is a conservative closure signal, not a spelling/grammar proof.
    Unpunctuated prose requires an explicit completeness verdict; a missing
    verdict remains unknown. Reviewed table rows and audited math retain their
    structural closure instead of being forced through prose punctuation.
    """
    text = re.sub(r"\s*\[\d+\]", "", text).strip().rstrip("*_`\"'\u201d\u2019")
    if not text or damaged_source_text(text) or semantic_complete is False:
        return False
    if semantic_complete is True:
        return True
    if structured:
        return True
    return text.endswith((".", "!", "?", "\u0964", "\u0965", "\u3002", "\uff01", "\uff1f"))
