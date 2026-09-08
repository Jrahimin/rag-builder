"""Summarize saved replay results without making additional provider calls."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


def expected_fact_presence(case: str, content: str) -> dict[str, bool]:
    """Smoke checks, not proof of arithmetic, attribution or rule applicability."""
    expected = {
        "gross": ["800000", "6000", "39000"],
        "taxable": ["110000", "6000", "104000"],
        "rebate": ["3%", "10%", "750000"],
        "film_conflict": ["1998", "1999"],
    }.get(case, [])
    normalized = "".join(
        str(unicodedata.decimal(char)) if char.isdecimal() else char
        for char in unicodedata.normalize("NFKC", content)
    )
    amounts = {item.replace(",", "") for item in re.findall(r"\d[\d,]*", normalized)}
    scales = {"lakh": 100_000, "lac": 100_000, "লাখ": 100_000, "লক্ষ": 100_000}
    for number, unit in re.findall(
        r"(\d[\d,]*(?:\.\d+)?)\s*(lakh|lac|লাখ|লক্ষ)", normalized, re.IGNORECASE
    ):
        amount = Decimal(number.replace(",", "")) * scales[unit.casefold()]
        if amount == amount.to_integral_value():
            amounts.add(str(int(amount)))
    return {
        fact: bool(re.search(rf"(?<!\d){re.escape(fact[:-1])}\s*%", normalized))
        if fact.endswith("%")
        else fact in amounts
        for fact in expected
    }


def summarize(paths: list[Path]) -> dict:
    results = {}
    for path in paths:
        groups = defaultdict(list)
        for record in json.loads(path.read_text(encoding="utf-8")):
            key = record["case"]
            if record.get("context_ceiling") is not None:
                key += f"@{record['context_ceiling']}"
            groups[key].append(record)
        cases = {}
        for case, records in groups.items():
            phases = {}
            for phase, subset in (
                ("first_case_turn", records[:1]),
                ("subsequent_case_turns", records[1:]),
                ("all", records),
            ):
                values = [r["elapsed_ms"] for r in subset if "elapsed_ms" in r]
                prep = [
                    a.get("metadata", {}).get("retrieval_time_ms", a.get("retrieval_latency_ms"))
                    for r in subset
                    if (a := r.get("assistant"))
                ]
                prep = [value for value in prep if value is not None]
                phases[phase] = {
                    "n": len(values),
                    "p50_ms": statistics.median(values) if values else None,
                    "p95_ms": statistics.quantiles(values, n=100, method="inclusive")[94]
                    if len(values) > 1
                    else None,
                    "preparation_p50_ms": statistics.median(prep) if prep else None,
                    "range_ms": [min(values), max(values)] if values else None,
                }
            cases[case] = {
                "timings": phases,
                "answered": sum(
                    bool(r.get("assistant"))
                    and not r["assistant"].get("insufficient_evidence_reason")
                    and r["assistant"].get("metadata", {}).get("turn_resolution", {}).get("outcome")
                    != "clarify"
                    for r in records
                ),
                "clarified": sum(
                    r.get("assistant", {})
                    .get("metadata", {})
                    .get("turn_resolution", {})
                    .get("outcome")
                    == "clarify"
                    for r in records
                ),
                "refused": sum(
                    bool(r.get("assistant", {}).get("insufficient_evidence_reason"))
                    for r in records
                ),
                "errors": sum("error_type" in r for r in records),
                "claim_verified": sum(
                    r.get("assistant", {}).get("grounded") is True for r in records
                ),
                "expected_fact_presence": [
                    expected_fact_presence(r["case"], r.get("assistant", {}).get("content", ""))
                    for r in records
                ],
                "provider_work": [
                    r.get("assistant", {}).get("metadata", {}).get("lifecycle", {}).get("counts")
                    for r in records
                ],
                "conversation_ids": [r.get("conversation_id") for r in records],
            }
        results[path.stem] = cases
    return {
        "version": "rag-enhancement-evaluation.v1",
        "caveats": [
            "First/subsequent case turns are process-order cohorts, "
            "not verified cold/warm provider caches.",
            "No database or provider caches were evicted. "
            "p95 is descriptive interpolation with very few samples.",
            "Refusals and errors are included in timing summaries and counted separately; "
            "faster refusals do not establish a quality-preserving speedup.",
            "Claim verification is separate from required evidence coverage "
            "and human answer-quality review.",
            "Expected-fact presence is a smoke check only; an answered turn or matching number "
            "does not establish correct arithmetic, attribution or applicability.",
        ],
        "runs": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summarize(args.results), indent=2) + "\n", encoding="utf-8")
