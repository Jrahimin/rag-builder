# Live RAG failure investigation and remediation guide

## Conclusion

The September 19 plan delivered useful instrumentation and recovery machinery, but the available live evidence did not demonstrate improved answer reliability or latency. Two presentation-routing defects and duplicate reranker accounting were reproducible at the investigated revision. The supplied company-compliance request failed during coverage verification after successful candidate discovery. It did not fail because retrieval found nothing.

The evidence below describes the deployed failure and local checkout `bd41f9d` (merge of PR 25) before remediation. A working-tree remediation was subsequently implemented and tested, as recorded in **Remediation status** below. It has not been deployed or confirmed against the live API. The API response identifies an index build, not a deployed Git commit; exact source/deployment equivalence remains unverified.

## Remediation status

Implemented in the working tree on 2026-09-21:

- The two observed Bangla presentation-only prompts now take deterministic presentation routing while retaining the existing reuse eligibility and claim-verification safeguards.
- Reranker provider work now has one append owner, eliminating the duplicate lifecycle records.
- Selector repair now sends a bounded request containing only failed checks and the relevant source lines when isolated repair is safe. Full-schema retries are labeled separately.
- Coverage failures now retain bounded selector diagnostics and terminal retry categories without storing document bodies or raw provider errors.
- Verification failure is presented distinctly from missing evidence. When no validated coverage list exists, response policy exposes the repair requirements as unresolved facets.
- Operator Journey, message status, and inspector wording now agree that retrieval succeeded but coverage verification failed.

Focused backend tests, frontend tests, Python lint/type checks, TypeScript compilation, and diff validation pass. Deployment and replay of the exact live request are still required before claiming production reliability or latency improvement.

## Evidence and limits

- User-supplied API response: conversation `a8bebc46-ff05-4e2d-9133-920b30f2770d`, assistant message `1a7069b3-a57c-40de-a2aa-d3bf963c646a`, created `2026-09-20T18:29:46Z`.
- Snapshot `ae041fbd-462b-414c-88d2-35f6f3732160`, project revision 13, index `d0317f95-a523-465f-9126-e5be93031283`, source metadata generation 101.
- Earlier live exploration: conversation `8eca3b71-f2db-4539-927f-ab49c4ec1e14`; one initial answer and two follow-ups. These are observations, not a repeated benchmark.
- Current source inspection and local reproduction; 228 tests passed across rewrite retrieval, request-work instrumentation, and evidence repair.
- No historical successful response for the same company-compliance prompt was supplied. A causal before/after regression or speed improvement cannot be established without that response and its effective configuration.

## Corrections to the earlier audit

1. The deployment configuration's `indexed_only` value is a default, not proof of a conversation's effective policy. Your response explicitly records `indexed_then_web`. Web acquisition in the earlier conversation is not, by itself, a confirmed policy violation. That conversation's snapshot must be inspected before making that claim.
2. The earlier tax answer's 550,000 figure was prematurely described as correct. The retrieved context included adjacent tax-year sections and different thresholds. A `verified` claim flag is not independent confirmation of year applicability. Inspect the governing heading preceding the cited Finance Act page 144 passage before accepting that answer. This report makes no legal determination of the correct threshold.
3. The second rewrite followed a refusal, not a fresh successful answer. It demonstrates poor handling of that conversation state, but is not an independent clean fast-reuse benchmark. The first rewrite immediately after the cited answer is the stronger live reproduction.
4. The project has 38 documents. Retrieval from Finance Act/guidance documents was legitimate project membership, not demonstrated cross-project leakage.

## What happened in the supplied request

The question asked for continuing obligations of an incorporated but non-operating company: filings, records, authorities, deadlines, and consequences. The planner created eleven requirements and eight searches.

| Work | Recorded duration | Interpretation |
| --- | ---: | --- |
| Initial retrieval | 2.861 s | Candidates returned successfully |
| Recovery planning | 19.243 s | Eleven requirements, eight queries |
| Coverage review | 26.481 s | 32,056 input tokens; invalid validation result |
| Selector retry | 15.892 s | 37,426 input tokens; no valid result accepted |
| Total server processing | 75.196 s | No answer-generation operation |

Planning plus review plus retry accounts for about 61.6 seconds, or 82% of total processing. Do not add all stage durations: retrieval branches and waits overlap. Aggregate database connection acquisition was only 59 ms, deployment lease acquisition 35 ms, and process semaphore waits zero. These measurements do not support server resizing or database-pool tuning as the first remedy. The 22.496 s sum of batch semaphore waits is aggregate branch waiting, not an extra serial wall-clock interval.

The trace contains nine ranked retrieval calls, nine actual rerank-count increments, eleven embedding calls, and three LLM calls. Each of the eight recovery branches admitted candidates (counts 12, 8, 9, 10, 7, 4, 8, 7 before subsequent branch selection/budgeting). These counts are not distinct sources and do not establish validated obligations. Nevertheless, they disprove a simple no-results explanation.

The terminal diagnostics are:

```json
{
  "phase": "coverage_review",
  "status": "repair_unavailable",
  "failure_reason": "invalid_model_response",
  "validation_errors": [{"loc": ["coverage"], "type": "value_error"}],
  "response_mode": "indexed_then_web",
  "web_status": "suppressed_unresolved_authority"
}
```

All eleven proof-map facets remained invalid with empty evidence lists. No validated partial checkpoint was available to return. Generation was zero milliseconds; the final content came from a deterministic failure message. `success: true` means the API operation returned a response, not that it answered the task.

## Root causes and confidence

### A. Coverage selector validation/retry failure: strong evidence, exact selector unknown

In `services/evidence_repair_service.py`, `_validated_completion()` explicitly raises a `ValidationError` at `loc=("coverage",)` when `_selector_range_failures()` finds an unknown/missing/blank selected source range. The response's error shape and `selector_retry` purpose strongly point to this path. A contradictory complete verdict uses a different custom error in `services/evidence_coverage.py`.

The retry can fail because its JSON is malformed, replacement identities do not match, required replacements are absent, or selectors still fail. Several branches re-raise the original exception. The persisted diagnostics retain only `type` and `loc`, so they cannot tell us which range failed or why the retry failed. Do not claim a particular off-by-one error or fabricate the model's original output.

The selector-only retry narrows the requested output but still resends the entire review exchange. `_structured_selector_retry()` converts numbered content into source-line records throughout the payload. In this trace input size rose from 32,056 to 37,426 tokens. Narrow output is not yet narrow input.

### B. Authority metadata limits usable evidence: confirmed contributing factor

The initial trigger was `unresolved_authority`. Trace metadata reports three unscoped authority relationships, two incomplete/ungoverned metadata exclusions, and eight stale/replaced revision exclusions. Affected tax relationships have empty `target_provisions`; some modifiers have no effective-from date.

Stale revision exclusions may be correct version filtering and should not be treated as errors automatically. Empty provision scopes and incomplete active modifier metadata do need source-backed review. They can prevent a tax rule from establishing current applicability, even when text is retrieved.

The existing policy suppressed web fallback under unresolved authority. This is consistent with the plan's preservation of authority suppression. Enabling unrestricted web fallback or disabling enforcement would change policy and would not fix selector validation.

### C. Bangla presentation routing: confirmed executable defect

Calling `rewrite_followup_mode()` locally with resolved follow-up context yields:

| Input | Result at investigated revision | Intended interpretation |
| --- | --- | --- |
| উপরের উত্তরটি নতুন কোনো তথ্য যোগ না করে ৩টি বুলেট পয়েন্টে লিখুন। | `adds_facts` | Presentation only |
| আগের উত্তরের তথ্য অপরিবর্তিত রেখে এক বাক্যে লিখুন। | `not_applicable` | Presentation only |
| Rewrite the previous answer in three bullets. Do not add new facts. | `presentation_only` | Presentation only |

`_NEGATED_ADDITION` recognizes only a few Bangla forms; it misses the first phrase, leaving `_ADDED_FACT` to match `নতুন ... তথ্য`. `_PRESENTATION_ACTION` misses the second phrase. Even after fixing negation, residual-token handling must recognize complete format instructions such as `৩টি`, `পয়েন্টে`, and `লিখুন`; simply expanding one regex is insufficient.

This explains why deterministic preflight is missed. It does not prove every later fallback decision: those require the persisted resolver/reuse diagnostics. The earlier live first rewrite did execute resolver, ranked retrieval, planner and web review, then refuse after 23.871 seconds.

### D. Reranker accounting: confirmed code defect

`RequestWork.annotate_provider_call()` appends a call if not already recorded. `HybridRetriever` invokes it before reranking, then appends the same object again in `finally`. One provider operation therefore appears twice. The supplied response contains 18 rerank records for nine `rerank_calls`. Provider cost/call analysis based on the list or purpose counts is inflated. This is not evidence of 18 actual API requests.

### E. Failure messaging mixes different meanings: confirmed

The main content correctly says verification failed and explicitly avoids claiming the law is absent. But the response also carries `finish_reason: insufficient_evidence`, reason `unresolved_authority`, and a notice saying there is not enough indexed evidence. These represent different layers—trigger, terminal failure, and outcome—without a clear hierarchy. `answerable_scope.unresolved_facets` is empty despite eleven invalid proof-map facets. That is poor diagnostic presentation, not proof that no facets remain unresolved.

## Is the plan working?

| Stage | Assessment from available evidence |
| --- | --- |
| 1A instrumentation | Substantially present: spans, purposes, bounded omission, wait measurements, client timing separation, persistence exclusion. Duplicate rerank accounting and sparse terminal validation diagnostics need correction. |
| 1B exact citation reuse | Implemented in source but fails the observed Bangla wording before eligibility validation. No successful live exact-reuse trace was collected. |
| 2 incremental recovery | Requirements, query ownership, proof map, caches, retry and checkpoint logic exist. This request failed on its first review, so there was no validated proof to retain. It does not demonstrate delta-review savings. |
| 3 grounding/authority | Not assessed by this request: no factual answer was generated. Earlier tax answer needs temporal-context review. |

The system is more observable. Reliability improvement and lower latency remain unproven. A previous answer could reflect a genuinely better path, a different snapshot/corpus, provider variability, or an answer that bypassed/failed substantive verification. Compare the prior proof and claims before treating refusal avoidance as success.

## Recommended implementation sequence and disposition

1. **Capture actionable validation categories — implemented locally.** Bounded requirement/query/evidence indexes, source-known state, selector range, line count, blank-range state, retry kind, and terminal retry category are retained. Deployed commit/release identity remains a follow-up because it needs release-pipeline ownership.
2. **Repair selector protocol — implemented locally.** Isolated selector repair uses only failed checks and their required source lines, validates replacements and the merged verdict, and keeps one bounded retry. Full-schema retry has a distinct telemetry purpose.
3. **Repair deterministic Bangla routing — implemented locally.** Phrase-level negation, transformation terms, Bangla numerals, and observed inflections are covered by exact regression tests. Existing reuse eligibility and final claim verification remain in force.
4. **Correct reranker recording — implemented locally.** `RequestWork.annotate_provider_call()` owns the append, and the retriever test asserts one lifecycle record for one provider operation.
5. **Repair active authority metadata separately.** Review the active modifier records for the income-tax documents, using official source provisions and dates. Record exact affected provisions where supported. Preserve unrelated company-law evidence. Do not mark every stale relationship erroneous or infer whole-document replacement from `MODIFIES`.
6. **Improve partial-proof resilience without relaxing policy.** Restore only validated checkpoints with unchanged dependencies. This trace has none, so it cannot safely produce a partial answer merely from admitted chunks. Measure whether the existing planning exchange can validate initial safe evidence, as the plan already allows. Any proposal for additional independent review calls or new partial eligibility is a separate change requiring its own cost and policy assessment.
7. **Run controlled comparison.** Use the exact company prompt and fresh conversations, plus the planned cited-answer → three-bullet rewrite → shorter/English rewrite chain. Run at least three repetitions per case at fixed effective configuration, index, metadata generation and provider settings. Report individual results, median/range, valid full/partial answer rate, first-token/done/refresh timing, per-purpose calls/tokens, unsupported claims, and failure categories. Three runs cannot establish p95. Include the prior successful response in comparison once retrieved.

## Messaging specification

For this exact failure, suggested user-facing copy:

> I found relevant sources, but the verification step failed before I could produce a supported answer. This does not mean the company has no obligations or that the information is missing. You can retry, or ask first about one area, such as annual company filings.

Show `Verification failed` as the primary status. Retain authority limitations as secondary context where applicable. Do not tell the user to upload more documents solely because a selector was invalid. A narrower question is a recovery option, not the engineering fix.

In operator diagnostics, show:

> Coverage verification failed after one retry. Candidate retrieval completed. No validated partial answer was available. Web fallback was suppressed because source authority remained unresolved.

Expose the sanitized error category and correlation/message IDs next to that message. Preserve backward-compatible existing fields while adding optional outcome/failure-stage fields, for example `outcome=verification_failed`, `failure_stage=coverage_review`, and a precise terminal retry code once known. Ensure notices, chat status, Journey result, and API metadata agree. An uncited answer must not be titled “Cited answer”.

Progress should describe the active operation: finding sources, checking applicability, verifying coverage, repairing a verification result, and preparing an answer. Use the existing progress event contract; no additional mandatory LLM calls or SSE event types are needed.

## Verification performed

```text
PYTHONPATH=backend
backend/venv/Scripts/python.exe -m pytest \
  tests/unit/modules/conversations/test_rewrite_retrieval.py \
  tests/unit/platform/test_request_work.py \
  tests/unit/modules/conversations/test_evidence_repair_service.py \
  -q --disable-warnings
```

The initial investigation result was 228 passing tests, while direct calls with the live Bangla prompts reproduced the two incorrect routing classifications.

After remediation:

- Focused backend conversation/retrieval suite passed, including the exact Bangla prompts, compact selector repair, failure diagnostics, response policy, notices, and single reranker recording.
- Ruff passed for every changed Python source and test file.
- Mypy passed for the five changed backend source files.
- TypeScript compilation passed.
- Frontend Test Lab and Message Inspector suites passed: 31 tests across two files.
- `git diff --check` passed.

These checks validate the local changes; they do not establish production quality until the change is deployed and the controlled live comparison is run.
