# PR 25: final review of the uncommitted refinements

Review date: 20 September 2026. Base HEAD: `9d34554ae8980469559fda567f92bf2c8e8a37d0`, plus the current uncommitted changes.

> Remediation status: the seven findings below were subsequently addressed in the
> same uncommitted worktree. Their descriptions are retained as the before-state
> and acceptance rationale. The implementation now has paired regressions for
> condition polarity, exact-reuse fallback scope, rewrite lineage, nested stream
> cleanup, real pool checkout observation, and inherited partial-answer UI state.

Scope: the approved `docs/plans/rag-latency-evidence-execution-plan-2026-09-19.md` and the preceding final-review guide. This is a review, not a remediation patch. Application code was not changed during this review. Some findings are remaining gaps in the broader PR rather than regressions introduced by the latest diff.

## Decision
asa
The review originally blocked finalization on the findings below. Those bounded
fixes are now implemented without changing the architecture: bounded orchestration,
conservative exact reuse, source applicability, retained proof, supported partial
answers, and additive telemetry remain intact. Finalization still depends on the
test and live-evidence gates at the end of this document rather than on green unit
tests alone.

The most important product requirement is consistency: a shorter answer must retain the original topic and limitations; a failed optimization must lead to current validation; faithful wording must not be labeled contradictory; contradictory wording must not be labeled supported.

## Findings, ordered by priority

### R1 — P1: condition parsing still certifies reversed meaning

Location: `backend/app/modules/conversations/grounding_service.py:2788`, `_necessary_condition_polarity()`, and its consumers around lines 2802–2837.

Reproduced through the public `GroundingService.map_claims()` path with the existing unit-test chunk helper and default `ChatConfig`:

| Evidence | Generated claim | Actual result |
| --- | --- | --- |
| Payment of the fee is required for the certificate to be valid. | Payment of the fee is **not** required for the certificate to be valid. | `supported`, `grounded=True` |
| The certificate is valid only if the fee is paid and the form is **not** signed. | The certificate is valid only if the fee is **not** paid and the form is signed. | `supported`, `grounded=True` |

The required-for-validity regex consumes `not` between `is` and `for` without assigning it to either polarity. Both versions therefore become `(False, False)`. For compound conditions, a single Boolean for the entire condition loses which predicate was negated; both contradictory versions become `(False, True)`. Equal polarity tuples then suppress the mismatch.

Fix within Stage 3: only normalize constructions whose proposition, condition, and negation scope can be aligned. Account for negated necessity itself. Compound or ambiguous forms outside the bounded grammar must remain unverified, not gain support from equal Boolean tuples. Do not expand this into a general theorem prover or add an entailment provider.

Acceptance: both reproductions must cease being supported; preserve positive only-if/unless/required paraphrases and add swapped-negation compound controls.

### R2 — P1: failed exact reuse loses the partial-answer contract

Location: `backend/app/modules/conversations/services/chat_service.py:1026`–`1134`, the failed-reconstruction branch and subsequent presentation-only gates.

Reproduction: reuse the setup from `test_rewrite_carries_validated_partial_scope_into_generation_and_policy`, but construct `ExactRecallRetrieval(cited_chunk, configuration_hash="z" * 64)`. The earlier answer covers the AGM duty, excludes the filing deadline, and requires company type. Ask “Make it shorter.”

Observed output metadata:

```text
presentation_reuse.status = fallback
presentation_reuse.reuse_failure_category = configuration_changed
presentation_reuse.new_coverage_review = true
response_policy.answerable_scope.partial = false
response_policy.answerable_scope.unresolved_facets = []
response_policy.answerable_scope.missing_inputs = []
inherited_coverage = absent
knowledge_repair.status = not_needed
```

Generation ran. The recorded provider calls contained answer generation but no coverage review. The successful exact path propagates the previous limitations; the fallback path does not. The turn remains `presentation_only`, so ordinary comparison/compliance/calculation/applicability/relevance review triggers remain disabled. Cited recall can also replace the evidence decision with `sufficient=True`.

This is not proof that every generated fallback answer invents facts. It is proof that the generation/policy contract loses known limits and reports review that did not happen. Final per-claim grounding cannot restore an omitted requirement or missing user input.

Fix within Stage 1B/Stage 2: distinguish a presentation request from successfully validated evidence reuse. Failed reuse must enter the appropriate current scoped assessment/recovery path. Carry prior exclusions and inputs as constraints to reassess, not as stale proof. Set `new_coverage_review` from actual execution. Preserve all existing web and authority restrictions.

Acceptance: run the partial-answer rewrite chain with valid reuse and with configuration, source/span, and authority invalidation. In each fallback, verify generation prompt, actual review calls, final scope, missing inputs, citations, and persisted metadata.

### R3 — P2: faithful wording containing “without” is rejected

Location: `backend/app/modules/conversations/grounding_service.py:2825`.

Reproduced with identical source and claim:

> The certificate is valid only if the fee is paid without delay.

`map_claims()` returns `unsupported`, verification method `bounded_entailment`. The guard treats any `without` or `always` in the claim as contradiction whenever the evidence expresses a necessary condition. Here `without delay` does not remove the fee condition.

Fix within Stage 3: compare the aligned changed condition rather than globally vetoing these words. Exact faithful statements and meaning-preserving modifiers must survive; “valid without paying the fee” against a fee requirement must still fail. Ambiguous differences should remain unverified rather than being declared contradictions.

### R4 — P2: a resolver-approved rewrite can replace the factual topic in the next turn

Location: `backend/app/modules/conversations/services/rewrite_retrieval.py:165` and `chat_service.py:843`.

Reproduced history: factual question “What goods may be sold?”, its answer, “Explain that simply.”, and its answer. `retained_rewrite_question(history)` returns **“Explain that simply.”**, not the factual question. The next “Make it shorter” therefore receives the wrong retained retrieval query.

The current turn can honor the resolver's presentation-only decision, but historical scanning reclassifies raw user text with the deterministic heuristic and no saved decision. Resolver-approved ambiguous transformations consequently become factual anchors. Successful exact reuse can hide this until a later fallback needs a meaningful retrieval query.

Fix within Stage 1B: retain the factual query and resolved transformation lineage explicitly using existing turn metadata/provenance, and consume that lineage on later turns. Do not infer that every short prior turn is a transformation. Preserve genuine changed-topic and changed-population turns.

Acceptance: factual question → resolver-approved audience/simplification rewrite → deterministic rewrite, with both intact citations and invalidated provenance. Assert the retained query and resolver/retrieval counts, not just the final rewrite label.

### R5 — P2: the SSE producer does not own nested stream cleanup end to end

Location: `backend/app/modules/conversations/services/chat_service.py:452` and `backend/app/api/v1/routes/conversations_router.py:295`.

The new producer correctly owns advancement/closure of its immediate iterator. However, both the route and `ChatService.stream_message()` wrap another async generator with `async for` without explicitly closing it when the wrapper closes or breaks.

Reproduced against the actual service wrapper with a controlled `_deliver_stream_message`: after one token, `await outer.aclose()` completed while the inner generator's `finally` had not executed. After yielding to the event loop, the inner finalizer ran in a different task. In that probe it retained copied request context, but it did not retain task ownership or awaited cleanup.

Thus successful direct-generator lifecycle tests do not establish full route → service → preparation/provider cleanup. Work can outlive producer teardown, and task-bound context resets remain vulnerable. This finding does not assert every disconnect leaks indefinitely.

Fix within Stage 1A: explicitly close each owned nested async iterator in the owner task, including the route's disconnect break. Preserve cancellation and await preparation/provider cleanup. Add full-wrapper tests for disconnect while preparing, while consuming tokens, and while blocked by the bounded queue; assert cleanup has completed before the response iterator closes.

### R6 — P2: database acquisition telemetry misses real acquisition and counts cached access

Location: `backend/app/platform/db/session.py:63` and `:105`–`:119`.

Read-only PostgreSQL reproduction with the real engine and a pool checkout listener:

```text
await session.stream(text("SELECT 1")); close result
  actual checkouts: 1; acquisition spans: 0
await session.scalar(text("SELECT 2"))
  actual checkouts: 1; acquisition spans: 1
```

The first acquisition bypasses the overridden methods. The later scalar records `connection()` access on an already checked-out connection as acquisition. `get`, streaming, and implicit ORM paths are not covered by the current transaction marker. The new integration test covers scalar/execute/commit, not these paths.

Fix within Stage 1A: observe the actual acquisition boundary consistently without pre-acquiring just for measurement. If a path cannot be instrumented reliably, expose the limitation instead of presenting cached-access duration as pool acquisition. Keep bind selection and transaction semantics unchanged. Do not infer infrastructure bottlenecks from the current incomplete measurements.

Acceptance: compare pool checkout events against telemetry for execute, scalar(s), get, stream/stream_scalars, flush/commit, repeat access, cancellation, and failure. Also test acquisition before request context attaches. Keep actual checkout counts unchanged relative to an ordinary session.

### R7 — P2: Test Lab does not recognize inherited partial answers

Location: `frontend/src/features/lab/TestLab.tsx:2212` and `:2884`.

The new classification reads only top-level `partial_answer` or `knowledge_repair.partial_answer`. Successful exact rewrites persist partial scope under `inherited_coverage.partial_answer`, with `response_policy.answerable_scope.partial=true` and `evidence_summary.coverage=partial`; their `knowledge_repair` is `not_needed`.

Consequently, a grounded, cited rewrite of a partial answer can satisfy the UI's complete-answer `passed` condition and lose its partial label. The backend does preserve scope on successful reuse; the UI fails to consume that representation. This is a code-path finding, not a live-browser reproduction.

Fix within Stage 1B/F8: use one backward-compatible partial-scope interpretation shared by submission classification and the inspector, including inherited scope and the canonical response policy. Preserve the distinction between useful partial and complete pass. Do not equate groundedness alone with usefulness.

Acceptance: original partial → shorten → translate, all grounded/cited, remains useful-partial and never increments complete-answer passes. Include old metadata shapes and message refresh.

## What the current refinements improve

- Source-policy document scoping no longer rewrites explicitly ineligible candidates as eligible, and scope capture retains enforcement. Keep this change; do not restore the bypass for usability.
- One-word residual factual requests now fall back to resolution instead of automatically receiving the presentation shortcut. Keep resolver-approved natural transformations, but fix historical lineage as described above.
- Retained-gap reconciliation now keeps omitted gaps and avoids the previous fuzzy closure of distinct requirements. The targeted proof-map tests pass. Add an explicit positive closure case when a gap is actually resolved; do not make silence count as resolution.
- The single SSE producer is an improvement for immediate iterator ownership. Extend that ownership through the existing wrappers.
- Project initialization no longer depends on the `rag-journey` audit actor. Keep normal and journey initialization aligned.
- Separating useful partial from complete success is the correct reporting direction. Complete the inherited-metadata handoff.

## End-to-end acceptance before sign-off

Use the existing journey tooling and fixed corpus/configuration; no new evaluation framework is required.

| Journey | Required observation |
| --- | --- |
| Greeting/thanks → factual request | Natural non-knowledge handling; normal factual retrieval resumes |
| Scoped current and explicit historical request | Eligible evidence remains usable; draft/future/expired/replaced exclusions remain effective in normal and exact recall |
| Independent multi-part request with one source gap | Supported parts delivered first; one concise, specific unresolved limitation |
| Calculation missing a material user input | Supported rules/intermediates retained; no invented final result; narrow input question when necessary |
| Factual → shorten → translate | Same factual anchor, citations, authority constraints, and partial limitations |
| Factual → ambiguous simplification → shorten | Resolver used as needed; subsequent factual anchor survives |
| Rewrite after identity/configuration/authority change | Scoped current fallback with real assessment; no stale-proof shortcut or erased limitations |
| Changed topic/population/date | Existing resolver and retrieval route; old citations do not establish new applicability |
| Stream disconnect/backpressure/provider failure | Nested cleanup awaited; no orphaned preparation/provider work; accurate request attribution |
| Persisted-message refresh and Test Lab | Final scope and claim status agree with backend; useful partial does not become complete pass |

The requested versatile experience should come from reliable handoffs and selective work, not looser truth criteria. Preserve `indexed_only`, `indexed_then_web`, `indexed_and_web`, authority suppression, and final verification. Streaming text is still provisional before final verification, as explicitly accepted by this plan; automatic post-stream rewriting is not a new requirement here.

## Verification actually performed in this review

- Re-ran **556 targeted unit tests**, all passing, across grounding, rewrite retrieval, chat service, evidence repair, conversation router, observed session, and source policy. Command: `backend/venv/Scripts/python.exe -m pytest tests/unit/modules/conversations/test_grounding_service.py tests/unit/modules/conversations/test_rewrite_retrieval.py tests/unit/modules/conversations/test_chat_service.py tests/unit/modules/conversations/test_evidence_repair_service.py tests/unit/api/test_conversations_router.py tests/unit/platform/test_observed_session.py tests/unit/modules/retrieval/test_source_policy.py -q --no-cov`.
- Ran additional in-memory public grounding probes for R1/R3, the retained-query probe for R4, and actual service-wrapper cleanup probes for R5.
- Ran an in-memory variant of the existing partial-rewrite service test for R2, changing only current configuration identity; inspected response metadata and recorded provider purposes.
- Ran read-only queries against PostgreSQL with actual checkout-event counting for R6. No schema or application-data changes were needed.
- Inspected the UI metadata consumers for R7; no new browser or frontend test run was performed in this review.

The reproductions above were ad hoc probes, not newly committed regression tests. Passing existing tests does not negate their failures. Earlier full-suite results are not a substitute for rerunning at the final corrected revision.

## Remaining release evidence and implementation order

1. Add failing regression tests for R1–R7 with paired useful-answer controls. Fix R1/R2 first, then the remaining bounded gaps. Avoid bundling unrelated architecture changes.
2. Validate the real PostgreSQL scoped-policy matrix: normal and exact recall for draft, future, expired, replaced, current, and historical cases. The current new observed-session integration test does not establish that matrix.
3. Re-run full backend/frontend/static/integration checks at the final revision.
4. Complete the plan's live acceptance: at least three repetitions per relevant fixed case, including the three-turn rewrite chain; individual runs and median/range; browser first-token/done/close/refresh timing; calls/tokens by purpose; supported content and limitations; Redis contention/cancelled waiters. These live checks were not performed in this review, so speedup and naturalness improvements remain unproven here.
5. Record one success and one fallback/failure trace per affected stage, final commit/configuration/corpus identity, compatibility impact, and rollback references. Keep usefulness/false-refusal outcomes separate from factual support and complete-answer pass rate.

After these bounded corrections and acceptance evidence, reassess against this list. No provider migration, reranker tuning, autonomous loop, durable proof graph, new web policy, or additional mandatory audit call is needed to resolve these findings.

## Post-remediation result

All seven code findings were addressed in the current worktree:

- condition verification now tracks negated necessity and predicate-level negation anchors while allowing faithful `without` modifiers;
- invalid exact reuse re-enters bounded coverage review with prior scope constraints, preserves unresolved inputs, and reports whether review actually ran;
- saved turn metadata carries the retained factual question across resolver-approved rewrite chains;
- the route and service explicitly close their owned nested async generators in the producer task;
- database telemetry observes real SQLAlchemy pool checkout events across execute, scalar, get, stream, flush, and commit paths without pre-acquiring a connection;
- Test Lab consumes inherited partial-answer metadata and displays its unresolved exclusions without counting it as a complete pass;
- the earlier source-policy, proof-map, and project-initialization corrections remain intact.

Post-remediation verification completed locally:

- full backend unit suite passed;
- full PostgreSQL integration suite passed, including the new real-pool observation test;
- source-policy current/historical matrix, scoped modifier behavior, and bounded indexed-identity integration tests passed;
- all 103 frontend tests passed when run serially;
- Ruff, mypy over 443 application files, frontend ESLint, TypeScript, Prettier, and `git diff --check` passed.

The first concurrent full frontend attempt exhausted local Node worker memory, and the simultaneously running integration attempt crashed during Argon2 setup. Both suites passed when rerun independently; no product assertion is based on the failed resource-contended runs. Live provider/browser/Redis repetitions described by the approved plan remain deployment acceptance evidence rather than local deterministic test coverage.
