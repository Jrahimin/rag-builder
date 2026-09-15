"""Persist bounded response audit; preserve the earlier source inventory and history."""

import json
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parents[2]
attachment = Path(r"C:\Users\user\.codex\attachments\87179d9f-2fef-446c-902e-ae35011f7738\pasted-text.txt")
answer = json.loads(attachment.read_text(encoding="utf-8"))["data"]["assistant_message"]
metadata = answer["metadata"]
audit = {
    "date": "2026-09-15",
    "baseline_local_commit": "59efb82",
    "status": "targeted_application_fixes_locally_verified_awaiting_deployment",
    "source_generation": 101,
    "policy_revision": 10,
    "scope": "Source-based conversational application; company/tax cases are the available live corpus, not a cross-domain benchmark.",
    "attached_response": {
        "conversation_id": answer["conversation_id"],
        "message_id": answer["id"],
        "result": metadata["knowledge_repair"]["status"],
        "grounded": answer["grounded"],
        "milliseconds": answer["total_latency_ms"],
        "input_tokens": answer["input_tokens"],
        "output_tokens": answer["output_tokens"],
        "claim_counts": dict(Counter(c["verification"] for c in answer["claims"])),
        "embedding": metadata["embedding"],
        "lifecycle_counts": metadata["lifecycle"]["counts"],
        "evidence_funnel": metadata["evidence_funnel"],
        "coverage": metadata["knowledge_repair"]["coverage"],
        "partial_scope": metadata["knowledge_repair"]["partial_answer"],
    },
    "fresh_tests": [
        {
            "test": "annual_compliance_en",
            "conversation_id": "eac40b9e-372c-4333-8257-cf5739613474",
            "milliseconds": 77589,
            "result": "cited_grounding_incomplete",
            "selected": 5,
            "notes": "Named sections 36/81/190 are retrievable; answer contains 18/15-month AGM, 21-day return and 30-day accounts filing rules. Broad-query omissions are not proof these provisions are absent from the index.",
        },
        {
            "test": "same_conversation_bangla_summary",
            "conversation_id": "eac40b9e-372c-4333-8257-cf5739613474",
            "milliseconds": 51981,
            "result": "cited_grounding_incomplete",
            "supported": 4, "claims": 6, "selected": 4,
            "notes": "Follows three-bullet Bangla request but first AGM bullet retains [1], which is now section 36; section 81 is [2]. Current-turn citation remapping needs improvement. Rewrite still performs expensive retrieval/recovery.",
        },
        {
            "test": "same_conversation_thanks",
            "conversation_id": "eac40b9e-372c-4333-8257-cf5739613474",
            "milliseconds": 1273,
            "result": "appropriate_non_knowledge_reply_ui_false_warning",
            "selected": 0,
            "notes": "Returns You're welcome with no evidence, but deployed inspector incorrectly warns of failed grounding. Local UI fix requires backend non_knowledge_turn=true and no claims/citations/refusal/partial scope.",
        },
        {
            "test": "tax_registration_en",
            "conversation_id": "e6fab706-2b2e-4564-97c8-e5e03a5ee12e",
            "milliseconds": 25640,
            "result": "grounded", "supported": 3, "claims": 3, "selected": 3,
        },
    ],
    "findings": {
        "ingestion_and_embedding": "Embedding identity matched (Cohere embed-v4.0, set 3, 1024 dimensions). Relevant Act provisions are indexed and retrievable in a focused live case. No evidence supports a model swap or reindex as the primary fix.",
        "retrieval": "Authoritative recovery discarded all initial selected evidence whenever initial review was skipped. Broad planning paired four bundled concepts across languages instead of using the existing eight-query budget for distinct needs. The attached funnel also reports 18 document-limit exclusions; cap=6 is preserved pending evidence-based tuning.",
        "relations_and_documents": "Real source gaps remain: 19 Draft sources are excluded as intended, unresolved authority relationships and current official procedure/fee/licence/VAT gaps remain separately documented. Do not solve them by accepting similarity alone or activating incomplete sources.",
        "grounding": "Some flags are genuine (wrong current citation); some are normalization defects (বার বৎসর, এক বৎসর, পাঁচ বৎসর). The attachment has 25 supported, 5 unverified and 10 unsupported claims, so its 0.125 unverified_claim_rate does not mean 87.5% of all claims are supported. Several unsupported segments are limitation prose, not fabricated legal duties.",
        "conversation": "Bangla follow-up works but repeats costly retrieval/recovery and can copy stale citation indices. Partial-answer instructions were unnecessarily legal-specific and encouraged repetitive limitations. Non-knowledge replies were mislabeled as grounding failures in the inspector.",
        "configuration": "Authoritative policy revision 10 remains appropriate for this legal project. Existing factual and multi-perspective paths must remain distinct for other corpora; no global switch or lower grounding threshold was applied.",
    },
    "targeted_code_fixes": [
        "Retain initially admitted safe evidence for final recovery proof; exclude unresolved units and reconcile later authority changes. Deduplicate different admitted spans of the same chunk before the mutation guard, without merging their text. Match final admitted units by exact chunk and content.",
        "Prefer independent search concepts within the existing eight-query budget rather than mandatory bilingual pairs of bundled obligations; preserve calculation and applicability requirements.",
        "Use domain-neutral partial-scope wording, one concise limitation, and explicitly distinguish reviewed-passage gaps from corpus absence.",
        "Tell follow-up generation that historical citation numbers are not current evidence identifiers; verify and remap every retained claim.",
        "Normalize statutory Bangla year spellings and বার only next to duration units; changed numeric durations still fail validation.",
        "Show neutral conversational-reply status for explicit non-knowledge messages without source claims; retain warnings when factual claims or refusal/partial evidence exist.",
    ],
    "validation": {"backend_focused_passed": 28, "backend_deselected": 172, "frontend_inspector_passed": 10, "typescript": "passed", "prettier": "passed", "ruff": "passed", "diff_check": "passed", "large_suite_run": False},
    "live_source_or_setting_changes": [],
    "latest_fixes_deployed": False,
    "ready_for_full_company_queries": False,
    "remaining": [
        "New code and prompt changes need deployment and a bounded live comparison; latency and answer-quality gains are not measured yet.",
        "Source-complete company compliance remains unproven; official procedure, fee, licence/VAT and authority-resolution gaps are preserved.",
        "Follow-up evidence reuse and broader multilingual/domain evaluation remain beyond this bounded patch. No modern-RAG competitive benchmark is claimed.",
    ],
}
artifact = root / "artifacts/company-readiness/response-audit-2026-09-15.json"
artifact.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
log_path = root / "business-upload-log.json"
log = json.loads(log_path.read_text(encoding="utf-8"))
old = log.get("latest_bounded_validation")
history = log.setdefault("bounded_validation_history", [])
if old and old.get("date") != "2026-09-15" and old not in history:
    history.append(old)
log["latest_bounded_validation"] = audit
log["status"] = audit["status"]
log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
report_path = root / "business-upload-report.md"
report = report_path.read_text(encoding="utf-8")
parts = report.split("\n\n")
parts[1] = "**Latest check: 15 September 2026, deployment 59efb82.** The attached broad query now gives a partial answer; it is not fully grounded. Four live messages isolated evidence-loss, follow-up citation and conversational-status defects. Targeted local fixes are recorded below. Tax regression passed 3/3 claims. No tax source, lifecycle or project setting changed; the new fixes await deployment."
report = "\n\n".join(parts)
heading = "## Application response audit — 15 September 2026"
if heading in report:
    report = report[:report.index(heading)].rstrip()
report += """

## Application response audit — 15 September 2026

The supplied API response (`844efe7d-66fa-47da-bf3e-416d442bcee8`, answer `6f6004b0-71d8-4f5a-a1c5-ca73e03e851e`) now produces a useful partial answer. It took 69.501 seconds, 122,453 input tokens, four LLM calls and nine rerank calls. There were 25 supported, five unverified and ten unsupported claims. The unverified-rate metric alone must not be read as total accuracy. Some unsupported segments describe limitations rather than legal duties.

| Live case | Result | Round trip |
|---|---|---|
| Sections 36/81/190 in English | Relevant rules found; grounding incomplete | 77.589 s |
| Same answer shortened in Bangla | Three bullets; 4/6 supported; stale citation number | 51.981 s |
| Thanks | Appropriate reply; console falsely reports grounding failure | 1.273 s |
| Tax registration, fresh English conversation | Grounded, 3/3 claims | 25.640 s |

The first three use conversation `eac40b9e-372c-4333-8257-cf5739613474`; tax uses `e6fab706-2b2e-4564-97c8-e5e03a5ee12e`. This is four messages, not a broad benchmark. The attached response supplies the broad-query baseline without another expensive repeat.

**Primary defect:** authoritative recovery cleared initially admitted evidence when it skipped initial review. It now retains safe units for the final exact proof and still removes unresolved/superseded evidence. Different admitted spans of the same chunk are deduplicated before reconciliation without combining text. Final admission provenance matches exact content. Focused tests cover retained evidence, excluded unresolved evidence and rediscovered chunks with different spans.

**Other targeted changes:** split independent requirements within the existing search budget; avoid mandatory bilingual pairs that crowd out distinct concepts; make partial-answer instructions domain-neutral and concise; require current-turn citation remapping on follow-ups; recognize statutory “বার বৎসর” and year variants; and suppress the inspector's false failure for explicitly classified non-knowledge replies without claims. Citation checks, authority guards, source lifecycles, model choices, retrieval thresholds and project policy revision 10 remain intact.

The follow-up copied [1] for AGM timing even though current [1] was section 36 and current [2] was section 81. That is a real attribution problem, not a reason to lower grounding thresholds. The year-normalization defect is different: official sections 181/182 use “বার বৎসর”, “এক বৎসর” and “পাঁচ বৎসর”, which should match their numeric English equivalents.

Embedding identity matches the active index (Cohere embed-v4.0, 1024 dimensions, set 3). Named statutory provisions are retrievable. The trace therefore does not establish that bad embeddings or indexing are the main cause. It does show 18 document-cap exclusions (configured cap six); that is a tuning candidate, not permission to increase all budgets. The 19 Draft exclusions are intentional. Unreconciled official source and relationship gaps remain as recorded in earlier sections.

The local backend selection passes 28 tests (172 deselected); the inspector file passes 10 tests. TypeScript, Prettier, Ruff and diff checks pass. Frontend dependencies were restored offline from the existing lockfile; the restricted shell could not resolve dependency links, so those frontend checks ran with approved filesystem access. No large suite was run. New fixes are **not deployed**, and their live latency/quality improvement is not yet measured. Full company compliance and cross-domain competitive readiness are not certified. Follow-up evidence reuse remains a performance opportunity; preserving the protected legal project's policy is separate from the application's factual and multi-perspective capabilities.
"""
report_path.write_text(report, encoding="utf-8")
