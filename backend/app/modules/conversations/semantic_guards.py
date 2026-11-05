"""Versioned bounded domain grammar, separate from general evidence orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import regex


@dataclass(frozen=True, slots=True)
class SemanticGuardResult:
    version: str
    outcome: Literal["not_applicable", "scoped", "unverifiable"]
    claim: str
    evidence: str


def evaluate_section_190(
    claim: str, evidence: str, clause_negation: regex.Pattern
) -> SemanticGuardResult:
    scoped = section_190_polarity_scope(claim, evidence, clause_negation)
    return SemanticGuardResult(
        "companies.section190.v1",
        "unverifiable"
        if scoped is None
        else "not_applicable"
        if scoped == (claim, evidence)
        else "scoped",
        scoped[0] if scoped is not None else claim,
        scoped[1] if scoped is not None else evidence,
    )


def section_190_polarity_scope(
    claim: str, evidence: str, clause_negation: regex.Pattern
) -> tuple[str, str] | None:
    """Exclude Section 190 fallback conditions from the filing duty's polarity.

    The Bangla filing sentence contains negative *conditions* (no AGM and no
    listed officer) alongside a positive duty to file.  Compare those conditions
    only when the English claim actually states the corresponding fallback.
    """
    if not (
        "ব্যালান্স শীট" in evidence
        and regex.search(r"রেজিষ্ট্রারের\s+নিকট\s+দাখিল", evidence)
        and "ত্রিশদিন" in evidence
    ):
        return claim, evidence

    no_agm = regex.compile(r"বার্ষিক\s+সাধারণ\s+সভা\s+অনুষ্ঠিত\s+হয়\s+নাই")
    if no_agm.search(evidence):
        evidence = no_agm.sub("বার্ষিক সাধারণ সভা অনুষ্ঠিত হয়", evidence)
        claim = regex.sub(
            r"\bif\s+no\s+(?:annual\s+general\s+meeting|agm)\s+is\s+held\b",
            "if the AGM is held",
            claim,
            flags=regex.IGNORECASE,
        )

    no_officer = regex.compile(r"যদি\s+কোম্পানীতে\s+এইরূপ\s+পদধারী\s+কেহ\s+না\s+থাকেন")
    if no_officer.search(evidence):
        officer_fallback = regex.compile(
            r"\bif\s+(?:the\s+company\s+has\s+)?"
            r"(?:none\s+of\s+(?:those|the)|no)\s+officers?\b"
            r"(?=\s*(?:[,;.]|$))",
            regex.IGNORECASE,
        )
        officer_condition = regex.search(r"\bif\b[^.;,]{0,100}\bofficers?\b[^.;,]{0,100}", claim)
        if (
            "director" in claim
            and officer_condition
            and (
                clause_negation.search(officer_condition.group())
                or regex.search(r"\b(?:unwilling|unavailable)\b", officer_condition.group())
            )
            and not officer_fallback.search(claim)
        ):
            # Absence of office holders is not the same as their unwillingness,
            # availability, or another qualified condition. Do not let matching
            # negative words certify a condition this bounded grammar cannot compare.
            return None
        # An affirmative officer condition is not the statute's director fallback.
        if not ("director" in claim and officer_condition and not officer_fallback.search(claim)):
            evidence = no_officer.sub("যদি কোম্পানীতে এইরূপ পদধারী কেহ থাকেন", evidence)
            claim = officer_fallback.sub("if the company has officers", claim)
    return claim, evidence
