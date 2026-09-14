"""Record the bounded check after deployment of 7a84c5e."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / "business-upload-log.json"
data = json.loads(path.read_text(encoding="utf-8"))
result = {
    "date": "2026-09-14",
    "baseline_local_commit": "7a84c5e",
    "previous_changes_deployed": "Reported by user; live duration-guard behavior observed.",
    "status": "bounded_check_complete_targeted_fixes_awaiting_deployment",
    "source_generation": 101,
    "policy_revision": 10,
    "live_source_or_setting_changes": [],
    "fresh_tests": [
        {"test": "original_business_en", "conversation_id": "7491d2bd-3a77-429b-bf15-04dd3d0c8126", "milliseconds": 64861, "result": "refused", "admitted": 9, "selected": 0, "reason": "unresolved_authority", "recovery": "repair_unavailable", "failure": "invalid_model_response", "validation": {"loc": ["coverage"], "type": "value_error"}},
        {"test": "statutory_deadlines_bn", "conversation_id": "90220c17-9089-4d44-a192-7173f6421823", "milliseconds": 34355, "result": "partial", "supported": 3, "claims": 12, "selected": 2, "notes": "Answer contains correct 18/15-month and 21-day deadlines; new guard does not recognize official spelled Bangla durations. This is a confirmed contributing verification regression, not proof every unsupported claim has the same cause."},
        {"test": "tax_registration_en", "conversation_id": "da3a19d5-4399-4dde-a18e-f6be7d600471", "milliseconds": 8018, "result": "partial", "supported": 2, "claims": 3, "selected": 9, "notes": "Answer still returns TIN/SIM/registration steps; same 2/3 support result as preceding smoke test. Not a fully passing tax certification."},
    ],
    "pinpointed_issues": [
        "Broad business recovery fails source-line proof validation before final generation. The API-facing diagnostics show a coverage value_error, not a transport failure. The response does not expose the precise rejected selector, so the model's reason for choosing it is not asserted.",
        "The deployed duration guard compared numeric answers with only numeric source durations. Statutory section 81 spells out পনের, আঠারো, ত্রিশ and নব্বই; section 36 uses একুশ. Equivalent digits were incorrectly treated as absent.",
    ],
    "targeted_code_fixes": [
        "On the existing single failed coverage retry only, replace exact matched numbered content strings with the existing structured source_lines records. Preserve question, dates, IDs, metadata and exact source text; do not duplicate context. Unknown or different source text remains untouched. Invalid second responses still fail closed.",
        "Normalize supported English and Bangla duration number words before quantity comparison, including the actual statutory spellings. Retain changed-quantity and unknown-page guards. No guessed unit conversions.",
    ],
    "validation": {"focused_tests_passed": 12, "deselected": 169, "ruff": "passed", "format": "passed", "diff_check": "passed", "large_suite_run": False},
    "latest_fixes_deployed": False,
    "ready_for_full_company_queries": False,
    "remaining": [
        "Latest fixes require deployment and a bounded live rerun; broad coverage-review reliability is not yet verified.",
        "Current RJSC name-clearance workflow conflict, forms/fee currency, licence/VAT and protected tax authority gaps remain as previously recorded. No unreliable source was activated to force a result.",
        "Number-word normalization covers the tested statutory spellings and supported vocabulary; it is not a universal Bangla number parser or proof of legal applicability.",
    ],
}
data["status"] = result["status"]
data["latest_bounded_validation"] = result
data["company_readiness_audit_2026_09_14"]["full_company_readiness_certified"] = False
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
report_path = root / "business-upload-report.md"
report = report_path.read_text(encoding="utf-8")
start = report.index("**Readiness")
end = report.index("\n\n", start)
report = report[:start] + "**Readiness is not certified. Latest bounded check: 3 live conversations and 12 focused local tests.** The broad business query still fails coverage proof validation. A duration-verification regression was also isolated and corrected locally. Two targeted fixes await deployment; no tax source, source lifecycle or proven setting changed. The latest section below supersedes earlier deployment-status statements." + report[end:]
report += """
## Latest bounded check after deployment of 7a84c5e

Testing stopped after three live conversations and twelve focused unit tests, as requested. No large suite ran. Source generation remains 101 and policy revision 10; no live metadata or configuration mutation was made.

| Test | Conversation ID | Result | Time |
|---|---|---|---|
| Original English business query | `7491d2bd-3a77-429b-bf15-04dd3d0c8126` | Withheld; 9 admitted / 0 selected | 64.861s |
| Bangla sections 81 / 36(3) | `90220c17-9089-4d44-a192-7173f6421823` | Correct core deadlines, partial; 3/12 supported | 34.355s |
| English tax registration | `da3a19d5-4399-4dde-a18e-f6be7d600471` | Answer returned; 2/3 supported, matching previous smoke result | 8.018s |

**Specific failures:** The broad query reaches evidence recovery but returns `repair_unavailable / invalid_model_response`, with `coverage / value_error` from source-line validation. This occurs before final generation; it is not a network failure. The API-facing diagnostic does not expose the exact rejected selector, so no claim is made about why the model chose it. Separately, the deployed duration guard rejected equivalent word/digit forms: the official Act uses পনের, আঠারো, ত্রিশ, নব্বই and একুশ, while answers use 15, 18, 30, 90 and 21. That regression is reproduced locally.

**Targeted implementation:** The existing single coverage retry now presents exact matched evidence as structured `source_lines` records instead of numbered strings. This reuses an existing representation; successful first-pass prompts and tax settings are unchanged. It preserves source text, IDs, metadata, question and dates, adds no duplicate context, and still rejects a second invalid response. Duration comparison now normalizes supported English/Bangla number words, including the statutory spellings, while retaining the changed-deadline guard and avoiding guessed unit conversions.

**Verification:** 12 focused tests passed (169 deselected); lint, formatting and diff checks passed. Tests cover the actual word/digit equivalence, continued rejection of a changed deadline, exact retry-context preservation, unknown/mismatched evidence remaining untouched, and repeated-invalid-range rejection. No broad test suite or repeated live refinement cycle ran.

These two fixes are **local, not deployed**. They do not establish a passing live broad-company answer. Existing RJSC workflow/forms/fee, licence/VAT and authority gaps remain documented. Full readiness remains false; the next required action is deployment and one bounded confirmation of the repaired paths, not another open-ended audit.
"""
report_path.write_text(report, encoding="utf-8")
print("Saved bounded validation: 3 live checks, 12 focused tests; no readiness claim.")
