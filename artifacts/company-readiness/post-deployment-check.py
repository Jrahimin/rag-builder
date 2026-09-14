"""Persist the 14 September resumed live audit; never rewrite source decisions."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / "business-upload-log.json"
data = json.loads(path.read_text(encoding="utf-8"))
tests = [
    dict(test="original_company_en", conversation_id="015781b4-df52-4e55-9c35-44e76e344021", milliseconds=64324, result="refused", admitted=9, selected=0, reason="unresolved_authority", recovery_failure="invalid_model_response", validation_error="json_invalid"),
    dict(test="original_company_bn", conversation_id="38f948fa-b6be-43fd-8289-0fc4c04f8dde", milliseconds=88143, result="refused", admitted=12, selected=0, reason="unresolved_authority", recovery_failure="invalid_model_response", validation_error="coverage value_error: invalid or blank source-line range after format retry"),
    dict(test="companies_sections_en", conversation_id="eaa0a79f-5bce-4227-8868-7fd1cb1b8e28", milliseconds=9281, result="grounded", supported=8, claims=8, selected=12, notes="Correct statutory sections 81 and 36(3), with schedule distinction; no model-article deadline substitution."),
    dict(test="companies_sections_bn", conversation_id="46fdca0c-6cad-44bd-a67a-5a04812df230", milliseconds=58966, result="partial", supported=11, claims=13, selected=2, notes="Correct statutory deadlines; comparative schedule context not selected. Not a complete bilingual readiness pass."),
    dict(test="tax_registration_en", conversation_id="f8e218f4-c4eb-4005-84fb-e9910e4351eb", milliseconds=7996, result="partial", supported=2, claims=3, selected=9, notes="TIN, biometric NID SIM and OTP/password answer returned. Not fully grounded."),
    dict(test="tax_registration_bn", conversation_id="6f3b676c-4ae5-4434-99d5-47bcbc47ad5c", milliseconds=6425, result="partial", supported=2, claims=3, selected=12, notes="TIN, biometric NID SIM and OTP/password answer returned. Review panel flags short list fragment '- আপনার **TIN**;' as unverified."),
]
followup = {
    "date": "2026-09-14",
    "status": "live_rechecked_remaining_readiness_gaps",
    "deployed_wording_fix_verified": True,
    "local_head_before_changes": "094d60f",
    "source_generation": 101,
    "active_build_prefix": "d0317f95",
    "inventory": {"ready": 38, "active": 19, "draft": 19},
    "policy_revision": 10,
    "evidence_approach": "authoritative",
    "query_translation": "off",
    "live_source_or_policy_mutations": [],
    "fresh_conversations": tests,
    "api_diagnosis": "Backend is available. Both broad retests reach recovery but fail structured proof validation. These specific runs are blocked by invalid model JSON/source-line output in addition to unresolved corpus authority and coverage; they are not transport failures or proof that no relevant passages exist. Existing format-only retry and exact-source validation were retained.",
    "official_rechecks": [
        {"url": "https://roc.gov.bd/pages/static-pages/6922dd32933eb65569e13e40", "result": "Full body now renders in browser; supersedes prior heading-only observation.", "content_updated": "2025-01-26", "decision": "Existing RJSC source remains Draft. Page says pre-registration name clearance is not applicable to private companies, but its private-company document checklist requires name clearance. Current workflow/exception cannot be inferred from this conflicting page."},
        {"url": "https://app.roc.gov.bd/help/fee_calculator.htm", "result": "Official help explains selecting business/entity type and entering authorised capital or mortgage amount. It supplies no fee schedule, VAT base or effective date; does not close current-fee completeness gap."},
    ],
    "local_fixes": [
        "Recognize English/Bangla page-qualified single/grouped citation markers only when all cited page numbers equal retrieved chunk page_number. Never use page numbers as source indexes or guess unknown/printed page mappings. Claim verification still runs.",
        "Do not mark a duration claim supported merely because wording is similar when its numeric day/month/year quantity is absent from cited evidence. Bangla and English digits/units align; unit conversions are not guessed.",
        "Give the existing single source-range format retry the specific source label, line count and available nonempty lines. Repeated invalid ranges still fail validation; no extra retry or relaxed proof requirement was introduced.",
    ],
    "code_validation": {"grounding_and_chat_tests_passed": 199, "evidence_repair_tests_passed": 82, "total_tests_passed": 281, "ruff_check": "passed", "ruff_format_check": "passed", "git_diff_check": "passed"},
    "new_code_deployed": False,
    "limitations": [
        "New code fixes are local and need deployment followed by fresh live tests; today's live results test the user's earlier deployment.",
        "No full incorporation/dormant-company readiness certification; current RJSC workflow/forms/fees and other licence/VAT/authority gaps persist.",
        "Tax answers still return but both fresh registration tests are only 2/3 grounded. Prior calculation limitations remain; no tax source or proven setting changed.",
        "Matching duration quantities is only a conservative guard, not proof of correct applicability, unit conversion, negation or legal scope.",
        "No individual conversation per historic title-only correction was added.",
        "Projects source selection again left the Income Tax Bangla correction form visible after clicking RJSC. No form values were edited or saved; the RJSC finding is recorded here, not claimed as a live metadata correction.",
    ],
}
data["status"] = followup["status"]
data["active_index_build_prefix"] = "d0317f95"
data["post_deployment_validation_2026_09_14"] = followup
audit = data["company_readiness_audit_2026_09_14"]
audit["status"] = followup["status"]
audit["live_blocker"] = "Backend restored; broad EN/BN recovery fails structured proof validation. See post_deployment_validation_2026_09_14."
audit["full_company_readiness_certified"] = False
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

report_path = root / "business-upload-report.md"
report = report_path.read_text(encoding="utf-8")
start = report.index("**Readiness")
end = report.index("\n\n", start)
report = report[:start] + "**Readiness is not certified. The backend is available again and the earlier wording fix is verified live.** Six fresh conversations completed after deployment. The broad dormant-company queries still fail evidence-review validation, while focused statutory retrieval improves. New citation and deadline-verification fixes are tested locally and await deployment. No tax source or proven setting was changed." + report[end:]
report = report.replace("It is **not deployed**, and does not repair missing authority or guarantee legal correctness.", "It is **now verified deployed** in both English and Bangla refusals. It does not repair missing authority or guarantee legal correctness.")
heading = "\n## Resumed post-deployment validation\n"
if heading in report:
    report = report[:report.index(heading)]
report += heading + "\nLive source generation remains **101**, with **38 ready documents (19 Active / 19 Draft)**. Active build prefix is `d0317f95`; conversation policy remains revision **10**, authoritative, translation Off. Earlier outage observations above are historical.\n\n"
report += "| Fresh test | Conversation ID | Result | Claims supported | Time |\n|---|---|---|---|---|\n"
for test in tests:
    counts = f"{test['supported']}/{test['claims']}" if "claims" in test else "No final generation"
    report += f"| {test['test']} | `{test['conversation_id']}` | {test['result']} | {counts} | {test['milliseconds']/1000:.3f}s |\n"
report += """
The English broad query admitted 9 passages and Bangla admitted 12; both selected zero final passages. Recovery failed with `invalid_model_response`: English returned invalid JSON; Bangla returned an invalid or blank source-line range after the existing format retry. Thus these runs expose a structured reviewer-output failure as well as unresolved legal coverage. Exact source verification and authority gates were not bypassed. The reviewer-output failure remains unresolved; the new citation fix runs later and cannot fix a refusal before generation.

The focused English statutory query passed 8/8 claims. The Bangla answer used the correct sections 81 and 36(3), but lacked the comparative schedule evidence and remained partial (11/13). Both tax registration answers returned the expected topic, yet only 2/3 claims were supported. Bangla review marked the short TIN list fragment unverified. This is not a fully passing tax regression or a new calculation certification.

### New local fixes awaiting deployment

- Page-qualified citation markers such as `[1, পৃষ্ঠা ১]` and grouped markers are recognized only when every page matches the retrieved chunk's page number. Unknown pages, mismatches, out-of-range references and ambiguous syntax are not normalized. Content verification remains mandatory.
- A changed day/month/year quantity cannot be marked supported solely through similar wording when that quantity is absent from cited evidence. The regression `90 days` versus a source's `21 days` now remains unverified. Bangla digits and units are handled without guessed conversions.
- The existing single source-range correction retry now receives the offending source label, line count and nonempty line numbers, rather than an ambiguous range-only error. Repeated invalid ranges remain blocked. This improves retry guidance; it does not prove the live reviewer failure is resolved.

Validation: **281 tests passed** (199 grounding/chat and 82 evidence-repair), including 10 new citation/deadline cases and repeated-invalid-range rejection; Ruff lint, formatting and diff checks passed. These edits are in `backend/app/modules/conversations/grounding_service.py`, `services/evidence_repair_service.py` and their unit tests. They are **not deployed** and have not been represented as live fixes.

### Official-source rechecks and remaining decisions

The [RJSC registration page](https://roc.gov.bd/pages/static-pages/6922dd32933eb65569e13e40) now renders its full body, updated 26 January 2025. This supersedes the earlier heading-only observation. However, it says private companies are excluded from pre-registration name clearance while requiring name clearance in the private-company checklist. The existing source `241c687a-144b-41a1-b18a-5f59518e8ea5` remains Draft until that conflict is resolved against the current workflow. No live metadata revision was saved: the Projects selection UI continued to display the tax correction form after the RJSC click, and that form was left untouched.

[Official fee-calculator help](https://app.roc.gov.bd/help/fee_calculator.htm) explains input selection but supplies no fee schedule, VAT base or effective date. It does not resolve the legacy form-fee/current-calculator conflict. Existing official forms and licence/compliance drafts remain subject to the earlier completeness and currency decisions.

Remaining work: repair and verify structured coverage-review reliability; deploy and live-test the new citation/deadline guard; complete current RJSC workflow/forms/fees and relevant licence/VAT evidence; resolve protected tax authority gaps without changing proven settings; obtain consistently supported bilingual answers. Full company-law readiness remains **false**.
"""
report_path.write_text(report, encoding="utf-8")
print("Updated JSON and report with six post-deployment conversations; readiness not certified.")
