"""Record the bounded live check of e63b393 without discarding earlier audits."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
log_path = root / "business-upload-log.json"
report_path = root / "business-upload-report.md"
log = json.loads(log_path.read_text(encoding="utf-8"))
record = {
    "date": "2026-09-14",
    "baseline_local_commit": "e63b393",
    "previous_changes_deployed": "Reported by user; fresh live responses checked.",
    "status": "bounded_check_complete_company_readiness_blocked",
    "source_generation": 101,
    "policy_revision": 10,
    "live_source_or_setting_changes": [],
    "fresh_tests": [
        {
            "test": "original_business_en",
            "conversation_id": "58bb96a2-fbbe-463b-bc30-942565f67f59",
            "milliseconds": 69279,
            "result": "refused",
            "initial": 100, "admitted": 9, "selected": 0,
            "reason": "unresolved_authority",
            "recovery": "repair_unavailable",
            "failure": "invalid_model_response",
            "validation": {"loc": [], "type": "value_error"},
            "notes": "Root CoverageVerdict consistency failure, distinct from prior source-range failure. The deployed diagnostics omit the exact invariant; do not infer which one failed. No answer generation occurred.",
        },
        {
            "test": "statutory_deadlines_bn",
            "conversation_id": "a6ef2366-2952-4340-8454-bcee7d0fc51a",
            "milliseconds": 10901,
            "result": "cited_grounding_incomplete",
            "supported": 4, "claims": 6, "selected": 12,
            "notes": "Correct core 18-month first AGM, 15-month maximum interval, 21-day annual return, and 30/90-day extension wording. Statutory sections 81 and 36 cited. Subsequent AGM and annual-return claims remain unverified; not a full pass.",
        },
        {
            "test": "tax_registration_en",
            "conversation_id": "72abfbad-b806-4813-a10f-210e31b3b159",
            "milliseconds": 8234,
            "result": "grounded",
            "supported": 6, "claims": 6, "selected": 12,
            "notes": "TIN, biometric mobile/NID, registration/password steps and overseas special registration returned with official citations. One focused tax regression only, not tax-calculation certification.",
        },
    ],
    "targeted_code_fixes": [
        "Give the three CoverageVerdict consistency invariants distinct Pydantic error types, preserved by existing API diagnostics without exposing model input. Validation and the single retry remain strict.",
        "For invalid_model_response recovery failures, explain source-verification failure in English/Bangla rather than assert missing amendment evidence. Genuine unresolved-authority wording remains unchanged.",
        "Recognize joined Bangla number-word durations such as statutory section 190's ত্রিশদিন; retain English word boundaries and changed-quantity guards.",
    ],
    "official_source_checks": [
        {
            "url": "https://roc.gov.bd/pages/static-pages/6922dbbc933eb65569e0c2d5",
            "official_title": "রিটার্ন ফাইলিং",
            "display_title": "RJSC — Returns Filing Guidance",
            "language": "bn", "page_updated": "2015-08-13",
            "decision": "not_uploaded_or_activated",
            "reason": "Annual filing subsection is useful, but the whole page is dated and contains suspect statutory references (including Form XXVIII references 12/191). Section 190 of active Act was checked separately. Do not promote the entire page as reconciled current authority.",
        },
        {
            "source_id": "f5cbfdfb-3aa6-4d92-8e7e-95e3c602af82",
            "url": "https://dncc.gov.bd/pages/static-pages/6922e06d933eb65569e27049",
            "official_title": "ট্রেড লাইসেন্স ইস্যু ও নবায়ন পদ্ধতি",
            "display_title": "DNCC — Trade Licence Issuance and Renewal Procedure (17 July 2025)",
            "language": "bn", "page_updated": "2025-07-17",
            "decision": "retain_draft_r2",
            "reason": "Original local capture is clipped. Fresh official page renders title/date but no procedure body, table, document image or iframe. A complete clean replacement could not be acquired from this page.",
            "relationships": "No relationship changes; DNCC local jurisdiction only.",
        },
    ],
    "validation": {
        "focused_tests_passed": 24, "deselected": 270,
        "ruff": "passed", "format": "passed", "diff_check": "passed",
        "large_suite_run": False,
        "notes": "Initial new diagnostic assertions used a cycling repair mock and failed; replaced with a bounded two-invalid-response test. Final focused selection passed.",
    },
    "latest_fixes_deployed": False,
    "ready_for_full_company_queries": False,
    "remaining": [
        "Broad live recovery still fails consistency validation before generation. Exact invariant cannot be reconstructed from the deployed generic value_error; new diagnostic codes are local only.",
        "Two Bangla claim-verification flags remain; no acceptance threshold was relaxed.",
        "Current RJSC name-clearance workflow/forms/fees, licence/VAT and protected tax-authority gaps remain as documented. Historic or incomplete sources were not activated to force answers.",
        "Local fixes need deployment; this run did not deploy or certify them live.",
    ],
}
history = log.setdefault("bounded_validation_history", [])
old = log.get("latest_bounded_validation")
if old and old.get("baseline_local_commit") != "e63b393" and old not in history:
    history.append(old)
log["latest_bounded_validation"] = record
log["status"] = record["status"]
log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

report = report_path.read_text(encoding="utf-8")
paragraphs = report.split("\n\n")
paragraphs[1] = (
    "**Readiness is not certified. Latest bounded check of deployed e63b393: three fresh live conversations and 24 focused local tests.** "
    "The broad business query still fails coverage consistency validation before answer generation. The Bangla deadline answer is substantively correct but only 4/6 claims are verified; the tax-registration answer passes 6/6. "
    "Narrow diagnostic, failure-message and joined-Bangla-duration fixes are local and await deployment. No live source, lifecycle, relationship or tax setting changed. The latest section supersedes earlier deployment-status statements."
)
report = "\n\n".join(paragraphs)
heading = "## Bounded check of deployment e63b393"
if heading in report:
    report = report[:report.index(heading)].rstrip()
report += """

## Bounded check of deployment e63b393

| Fresh conversation | Result | Time |
|---|---|---|
| Original business EN — `58bb96a2-fbbe-463b-bc30-942565f67f59` | Refused: root coverage `value_error`, 9 admitted, 0 selected | 69.279 s |
| Sections 81/36 BN — `a6ef2366-2952-4340-8454-bcee7d0fc51a` | Correct core deadlines; 4/6 claims supported | 10.901 s |
| Tax registration EN — `72abfbad-b806-4813-a10f-210e31b3b159` | Grounded; 6/6 claims supported | 8.234 s |

The broad query now fails a root coverage-consistency invariant, not the previously recorded range selector error. The response exposes only `loc: []` and `type: value_error`; it cannot tell us whether gap classifications, missing-input self-classification or completeness contradicted the evidence. A transport-success envelope is not answer success. No final answer was generated. Local changes give those invariant failures distinct safe error codes and make the refusal accurately describe a verification failure instead of asserting missing legal amendments. Strict proof validation and the existing one-retry limit remain intact; these changes do not repair or certify the model's underlying verdict.

The active statutory text's section 190 contains the joined spelling “ত্রিশদিন”. The duration checker now accepts that spelling as equivalent to 30 days. Focused tests preserve rejection of changed deadlines and unbound evidence. The final selection passed **24 tests**, with 270 deselected; lint, formatting and diff checks passed. No large suite or repeated broad live test was run.

The [official RJSC returns page](https://roc.gov.bd/pages/static-pages/6922dbbc933eb65569e0c2d5), official title “রিটার্ন ফাইলিং”, is dated 13 August 2015. Its annual-filing subsection is useful, but suspect cross-references elsewhere (including Form XXVIII references 12/191) prevent treating the entire page as reconciled current authority. It was not uploaded or activated.

The [official DNCC procedure page](https://dncc.gov.bd/pages/static-pages/6922e06d933eb65569e27049) shows “ট্রেড লাইসেন্স ইস্যু ও নবায়ন পদ্ধতি” and a 17 July 2025 update date, but its current procedure body is empty, with no table, document image or iframe. It cannot provide a clean replacement for the clipped local capture. Source `f5cbfdfb-3aa6-4d92-8e7e-95e3c602af82` remains Draft r2 with its existing language, dates and DNCC jurisdiction. No metadata or relationship mutation was made.

Still unresolved: broad recovery reliability, the two Bangla grounding flags, current RJSC workflow/forms/fee reconciliation, and licence/VAT/protected tax-authority gaps recorded above. The latest three local corrections require deployment; this run does not certify broad company-law readiness. Tax sources and policy revision 10 were preserved.
"""
report_path.write_text(report, encoding="utf-8")
