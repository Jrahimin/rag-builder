"""System-rendered structured notices attached to assistant messages.

Notices are NOT LLM text, NOT answer claims, and NOT citations.  They are
machine-readable metadata that integrators may render alongside the answer.
The registry provides EN/BN text for each kind; no other languages are produced
at this time.  Bumping text wording is a Git change — there is no runtime
version knob for notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Notice kinds
# ---------------------------------------------------------------------------


class NoticeKind:
    """Known notice kind identifiers."""

    SCOPE_EXCLUDES_EFFECTIVE_MODIFIER = "scope_excludes_effective_modifier"
    """Hard-scope request: the effective modifier for the asked-about provision
    is excluded by the document scope.  The answer is drawn from scoped evidence;
    the modifier is listed as ``source`` metadata."""

    WEB_EVIDENCE_USED = "web_evidence_used"
    """Some or all of the answer is drawn from web evidence rather than indexed
    knowledge."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    """No sufficient evidence was found to answer the question; the answer is a
    refusal."""


# ---------------------------------------------------------------------------
# Notice data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Notice:
    """One system-rendered notice attached to an assistant message."""

    kind: str
    language: str
    text: str
    source: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "language": self.language,
            "text": self.text,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Registry: EN/BN text for each kind
# ---------------------------------------------------------------------------

_EN_TEXTS: dict[str, str] = {
    NoticeKind.SCOPE_EXCLUDES_EFFECTIVE_MODIFIER: (
        "The answer is drawn from the requested document scope. "
        "A more recent amendment modifies provisions in this document and may "
        "supersede values shown here."
    ),
    NoticeKind.WEB_EVIDENCE_USED: (
        "Part or all of this answer uses current web sources, not indexed knowledge."
    ),
    NoticeKind.INSUFFICIENT_EVIDENCE: (
        "There is not enough indexed evidence to answer this question."
    ),
}

_BN_TEXTS: dict[str, str] = {
    NoticeKind.SCOPE_EXCLUDES_EFFECTIVE_MODIFIER: (
        "উত্তরটি অনুরোধ করা document scope থেকে নেওয়া হয়েছে। "
        "একটি সাম্প্রতিক সংশোধনী এই document-এর বিধানগুলি সংশোধন করে এবং "
        "এখানে দেখানো মানগুলি পরিবর্তন করতে পারে।"
    ),
    NoticeKind.WEB_EVIDENCE_USED: (
        "এই উত্তরের কিছু বা সব অংশ indexed knowledge নয়, বর্তমান web সূত্র থেকে নেওয়া হয়েছে।"
    ),
    NoticeKind.INSUFFICIENT_EVIDENCE: ("এই প্রশ্নের উত্তর দেওয়ার জন্য যথেষ্ট indexed evidence নেই।"),
}


def _notice_text(kind: str, language: str) -> str:
    texts = _BN_TEXTS if language == "bn" else _EN_TEXTS
    return texts.get(kind, kind)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def scope_excludes_effective_modifier_notice(
    *,
    language: str,
    modifier_records: list[dict[str, Any]],
) -> Notice:
    """Build a notice for a hard-scope request where the effective modifier is excluded.

    ``modifier_records`` should be the effective modifier MODIFIES records from
    retrieval diagnostics.  Only provenance metadata is included; no claim text.
    """
    source: dict[str, Any] = {
        "excluded_modifier_count": len(modifier_records),
    }
    if modifier_records:
        first = modifier_records[0]
        # Include stable provenance fields; never include LLM-derived text.
        for field in (
            "modifier_document_id",
            "modifier_revision_id",
            "modifier_effective_from",
            "target_provisions",
        ):
            if first.get(field) is not None:
                source[field] = first[field]

    return Notice(
        kind=NoticeKind.SCOPE_EXCLUDES_EFFECTIVE_MODIFIER,
        language=language,
        text=_notice_text(NoticeKind.SCOPE_EXCLUDES_EFFECTIVE_MODIFIER, language),
        source=source,
    )


def web_evidence_used_notice(*, language: str) -> Notice:
    return Notice(
        kind=NoticeKind.WEB_EVIDENCE_USED,
        language=language,
        text=_notice_text(NoticeKind.WEB_EVIDENCE_USED, language),
        source={},
    )


def insufficient_evidence_notice(*, language: str) -> Notice:
    return Notice(
        kind=NoticeKind.INSUFFICIENT_EVIDENCE,
        language=language,
        text=_notice_text(NoticeKind.INSUFFICIENT_EVIDENCE, language),
        source={},
    )
