"""Append this deployment's bounded findings without rewriting earlier evidence."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
record = {
    "date": "2026-09-15",
    "baseline_local_commit": "1fe315e",
    "status": "bounded_deployment_review_complete_new_fixes_awaiting_deployment",
    "source_generation": 101,
    "policy_revision": 10,
    "deployed_repair_version": "v18-retained-authoritative-evidence",
    "new_repair_version": "v19-bounded-planning-and-reference-review",
    "fresh_tests": [
        {"test": "original_business_en", "conversation_id": "72fb5ff8-7a74-4464-ae16-e687adae87eb",
         "milliseconds": 43238, "result": "refused", "selected": 0, "admitted": 9,
         "recovery": "incomplete_plan", "llm_calls": 1, "output_tokens": 2048,
         "reasoning_tokens": 2048, "retained_initial_count": 0,
         "notes": "Planner exhausted its output allowance before producing JSON. No recovery queries or answer generation ran. Retry cap equalled initial cap, disabling truncation retry."},
        {"test": "statutory_deadlines_en", "conversation_id": "735c3e0f-dd90-4d58-bad1-00b0c23e64e6",
         "milliseconds": 51458, "result": "grounded_partial", "supported": 10, "claims": 10,
         "selected": 4, "retained_initial_count": 11,
         "notes": "Retained-evidence fix works. Reviewer corrected planner's swapped section 36/81 mappings but left mistaken original requirements as missing, creating a false partial label."},
        {"test": "same_conversation_bangla_summary", "conversation_id": "735c3e0f-dd90-4d58-bad1-00b0c23e64e6",
         "milliseconds": 51351, "result": "partial_grounding_incomplete", "supported": 1, "claims": 5,
         "selected": 3, "notes": "Re-search lost the prior annual-return passage. Answer withheld its 21-day rule and still cited section 183 for the section 190 filing claim. Prompt-only remapping was insufficient."},
        {"test": "tax_registration_en", "conversation_id": "e39486da-5681-46f8-a1dd-6810ed494909",
         "milliseconds": 30178, "result": "cited_grounding_incomplete", "supported": 1, "claims": 3,
         "selected": 2, "notes": "Correct TIN/biometric SIM requirements remain. TIN fragment is unverified; uncited official-source label unsupported. Not a full grounding pass."},
        {"test": "thanks", "conversation_id": "e39486da-5681-46f8-a1dd-6810ed494909",
         "milliseconds": 1314, "result": "conversational_reply_correctly_classified", "selected": 0,
         "notes": "Deployed UI now shows Conversational reply, source verification not required, and View response details instead of a false grounding failure."},
    ],
    "targeted_code_fixes": [
        "Authoritative planning starts at up to 2048 tokens and can retry once at up to 4096, bounded by the configured output cap. Truncated JSON is never accepted. Record planning_finish_reason.",
        "Incomplete planning is described as a verification failure rather than asserted absence of legal evidence, in English and Bangla.",
        "Coverage prompts allow correcting planner-only reference errors under the same requirement ID, using original question and source proof. Requested topics and authority checks cannot be dropped.",
        "For resolved follow-ups explicitly requesting a rewrite/summary with no new information and no temporal change, re-query prior cited chunk IDs first. Current project/build, lifecycle, metadata, document and date filters remain in force; normal evidence admission and proof still run. Empty results fall back once to ordinary scoped search.",
        "The rewrite path uses bounded validated UUIDs from the immediately preceding assistant's stored citations, never previous answer text as evidence. Legacy adapters keep their existing call signature. Persist rewrite_recall diagnostics in the API metadata.",
    ],
    "validation": {"focused_tests_passed": 27, "deselected": 210, "ruff": "passed",
                   "format": "passed", "diff_check": "passed", "large_suite_run": False,
                   "cases": "Planner truncation/cap, proof-backed reference correction, rewrite routing/filter preservation/fallback, bounded current-build retrieval, existing follow-up/correction/zero-history regressions, and EN/BN failure wording."},
    "live_source_or_setting_changes": [],
    "latest_fixes_deployed": False,
    "ready_for_full_company_queries": False,
    "remaining": [
        "New planner and cited-retrieval fixes need deployment; their live improvement is not yet verified.",
        "Residual short-fragment and citation-label verification flags remain; no threshold was lowered to hide them.",
        "Current official company-procedure/forms/fees/licence/VAT and protected tax-authority gaps remain as previously documented. No Draft was promoted or source relationship changed.",
        "Cross-domain competitive readiness and tax calculation correctness are not established by this bounded check.",
    ],
}
path = root / "artifacts/company-readiness/deployment-check-1fe315e.json"
path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
log_path = root / "business-upload-log.json"
log = json.loads(log_path.read_text(encoding="utf-8"))
old = log.get("latest_bounded_validation")
history = log.setdefault("bounded_validation_history", [])
if old and old.get("baseline_local_commit") != "1fe315e" and old not in history:
    history.append(old)
log["latest_bounded_validation"] = record
log["status"] = record["status"]
log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
report_path = root / "business-upload-report.md"
report = report_path.read_text(encoding="utf-8")
parts = report.split("\n\n")
parts[1] = "**Latest bounded check: deployment 1fe315e, 15 September 2026.** Five live messages confirmed retained company evidence and the corrected conversational-status UI. The broad query stopped at planner token exhaustion; the Bangla rewrite still lost prior evidence. Three focused areas were corrected locally: bounded planning retry, planner-reference correction, and prior-citation recall for explicit fact-preserving rewrites. 27 focused tests pass. New fixes await deployment; full readiness is not certified. Tax sources and project settings remain unchanged."
report = "\n\n".join(parts)
heading = "## Deployment check 1fe315e — 15 September 2026"
if heading in report:
    report = report[:report.index(heading)].rstrip()
report += """

## Deployment check 1fe315e — 15 September 2026

| Live case | Result | Time |
|---|---|---|
| Original business EN | Refused: planner exhausted 2,048 tokens, all reasoning | 43.238 s |
| Sections 36/81/190 EN | 10/10 claims supported; incorrectly partial due to planner references | 51.458 s |
| Same answer shortened in Bangla | 1/5 supported; prior annual-return source lost | 51.351 s |
| Tax registration EN | Expected TIN/SIM requirements; 1/3 claims supported | 30.178 s |
| Thanks | Correct neutral conversational status | 1.314 s |

Conversation IDs and detailed decisions are in [deployment-check-1fe315e.json](artifacts/company-readiness/deployment-check-1fe315e.json). No broad-query retry or large suite was run.

The broad query did not reach recovery retrieval or generation. Its single LLM call consumed 2,048 output tokens, all reported as reasoning, and recovery returned `incomplete_plan`. The configured truncation retry had the same 2,048-token ceiling as the first attempt, making it unreachable. Local code now permits the existing single retry at up to 4,096 tokens, never above the configured output cap. A capped/truncated response still fails closed. The API now records the planner finish reason, and incomplete planning gets an accurate processing-failure message.

The retained-evidence fix is confirmed live: the focused statutory question retained 11 initial passages and all ten generated claims were supported. Its partial label arose because the planner assigned AGM to section 36 and annual return to section 81, then the reviewer created new correct requirements while leaving the mistaken ones unresolved. Coverage instructions now correct planner-only attribution errors under their original IDs, using source proof, without dropping actual user requirements.

The Bangla rewrite showed that prompt-only citation remapping is insufficient when retrieval loses prior evidence. A narrow, domain-neutral path now searches the preceding answer's cited chunk IDs first for resolved, explicit fact-preserving rewrites with no temporal change. It does not reuse the previous answer as factual evidence. Current project/build and source-policy filters, document/metadata/date constraints, relevance admission and final proof all remain active. Missing current passages trigger one normal scoped search. Topic changes, corrections, resolver fallback, unsupported adapters and ordinary questions retain the existing path. `rewrite_recall` metadata identifies the route taken.

Tax content still states the expected TIN and biometric SIM prerequisites, but the short TIN bullet was unverified and the uncited source-label line unsupported; this is not a fully passing tax-grounding result. The deployed Thanks response now correctly avoids a false grounding warning. Neither finding warrants weakening legal authority checks.

**Validation:** 27 focused backend tests passed, 210 deselected; Ruff, formatting and diff checks passed. These cover bounded truncation, output caps, valid and invalid corrected proofs, rewrite eligibility and fallback, current-build/filter preservation, and existing follow-up/correction/history behavior. No frontend changes were needed this turn. The new code is not deployed and no live performance improvement is claimed yet. Existing official-source and legal-relationship gaps remain; all source lifecycles and tax settings were preserved.
"""
report_path.write_text(report, encoding="utf-8")
