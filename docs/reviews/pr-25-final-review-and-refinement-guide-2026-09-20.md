# PR 25 final review and refinement guide

Review date: 20 September 2026  
Audience: Cursor or Grok implementing the final remediation  
Reviewed commit: `9d34554ae8980469559fda567f92bf2c8e8a37d0`  
PR: https://github.com/Jrahimin/rag-builder/pull/25  
Author: Codex independent review

## Decision in brief

Keep the architecture and finish the targeted fixes. Do not solve false refusals by weakening source applicability, permitting stale reuse, or treating contradictory claims as supported. Improve usability by answering independently supported parts, preserving conversational context, and sending uncertain shortcuts through the normal path.

The user's concern is valid: a system can pass safety tests while becoming frustrating to use. My earlier review emphasized unsafe acceptance more than unnecessary rejection. This final review adds explicit positive-path and conversational acceptance requirements. The four reproduced correctness defects remain MUST FIX; they do not require broad new refusal rules.

**The desired behavior is to preserve useful answers while limiting unsupported conclusions.** A failed exact-reuse check is a routing outcome, not an answer refusal. Missing one independent facet is not evidence that nothing can be answered. A low lexical score is not proof of contradiction. Conversely, fluent wording is not proof that a changed condition or population is supported.

I would not assign a numerical quality improvement or speedup yet. The implementation has a strong foundation, but the reproduced errors prevent a production-ready assessment. Expected benefits are fewer unsafe confirmations, fewer unnecessary repeated checks, better partial answers, and trustworthy latency measurements. Their size must be measured.

## Review basis and limits

The authoritative scope is [the approved execution plan](../plans/rag-latency-evidence-execution-plan-2026-09-19.md). Recommendations below refine its existing routing, proof-retention, partial-answer, verification, and instrumentation mechanisms. They do not authorize a new retrieval policy or a framework migration.

The preceding independent review inspected the actual PR code and tests and reproduced the routing, source-policy, grounding, delta-merge, and SSE-context failures described below. This follow-up rechecked the local commit and key generation, web-policy, grounding, and partial-answer paths. It is not a second complete rerun of the entire review. Code was unchanged before this document was created.

Verified results from that review: 985 targeted tests passed locally. CI reported 1,506 unit tests passed, 115 integration tests passed, and 101 frontend tests passed, with two optional PaddleOCR tests skipped. PostgreSQL-backed integration ran successfully. Live provider benchmarks, browser timing validation, and Redis contention were not demonstrated. Green tests establish tested behavior, not comprehensive correctness.

## Current practice and the appropriate balance

There is no single universal modern RAG design. Current primary documentation supports several patterns, with different control and latency tradeoffs:

- **Use the simplest workflow that meets the task.** Anthropic describes deterministic workflows and autonomous agents as different tools and recommends adding complexity only when it demonstrably helps. For this PR, retain the bounded pipeline and reuse shortcuts; do not add a general agent loop. [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- **Combine retrieval with selective validation.** LangChain documents two-step, agentic, and hybrid RAG. Hybrid designs can refine queries, validate retrieval, and check answers while retaining execution control. This repository already follows that broad pattern. More checks on every turn are not inherently better. [RAG architectures](https://docs.langchain.com/oss/python/deepagents/retrieval)
- **Improve evidence context before relaxing correctness.** Anthropic's contextual retrieval work addresses information lost when passages are separated from their documents. Here, retain the existing heading, table, passage, and authority context. A reindexing experiment can be considered later; it is not a requirement for this PR. [Contextual retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- **Evaluate multiple dimensions.** Ragas distinguishes retrieval precision/recall, response relevance, faithfulness, and accuracy. An application that optimizes only groundedness can become unhelpfully silent. Evaluate practical usefulness and unnecessary refusals alongside factual support and latency. No new evaluation package is required. [Available evaluation metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)

These sources describe available engineering approaches, not a measured industry-wide consensus or proof that one technique will improve this corpus. The following response behavior is my recommendation for this architecture.

## Scenario behavior to preserve

| Scenario | Desired behavior | Boundary that remains |
| --- | --- | --- |
| Greeting, thanks, or conversational acknowledgement | Respond naturally through the existing non-knowledge route | Do not add uncited domain assertions |
| Shorten or translate an unchanged previous answer | Reuse validated citations and partial scope; preserve language and formatting requests | No added facts or changed applicability |
| Ambiguous rewrite such as “for beginners” | Use context and the existing resolver if needed; often answer without asking the user | Uncertainty about shortcut eligibility must not cause refusal |
| “Summarize penalties” after a different topic | Resolve the new topic and retrieve normally | Old evidence must not be treated as proof of a new topic |
| “Does this apply to partnerships?” | Reuse conversational references, retrieve the changed applicability evidence | Previous population does not establish the new population |
| Three independent questions with evidence for two | Answer the two supported parts, then state the unresolved part briefly | Do not label the whole question complete |
| Final calculation lacks a rate or applicability condition | Explain supported rules or independent intermediate values; ask for a necessary user input if appropriate | Do not assume an unknown input is zero or compute an unsupported final amount |
| Citation becomes invalid or configuration changes | Fall back to current scoped retrieval and validation | Never reuse stale proof solely to preserve fluency |
| User selects an obsolete document | Preserve current policy; explain why it cannot establish the current rule | A document filter must not override applicability |
| User asks what an old document said at a specified date | Use existing explicit historical semantics if eligible | Do not equate historical description with current applicability |
| One generated claim has uncertain support | Preserve its honest verification status and other supported claims | Uncertainty must not become either automatic contradiction or automatic support |
| Retrieval fails or recovery reaches its budget | Return any already validated independent scope allowed by policy, or explain the specific unavailable evidence | Do not claim the corpus lacks a rule merely because retrieval failed |

“For beginners” and “for minors” can both be ambiguous: either audience adaptation or a change of applicability. The fast path should not decide that from one leftover word. The resolver can use the conversation to decide whether retrieval or a narrow clarification is needed.

## Changes in short

| ID | Priority | Change | Why |
| --- | --- | --- | --- |
| F1 | MUST FIX | Preserve source-policy exclusions under document scope | Prevent draft, expired, or replaced evidence becoming applicable |
| F2 | MUST FIX | Make presentation shortcuts conservative with normal fallback | Preserve natural conversation without reusing evidence for new facts |
| F3 | MUST FIX | Compare condition polarity and implication correctly | Stop reversed conditions receiving supported status |
| F4 | MUST FIX | Retain unresolved gaps consistently across delta merges | Prevent incomplete proof becoming complete through omission |
| F5 | SHOULD FIX | Bind streaming work to its actual task lifecycle | Restore request attribution and reliable cleanup |
| F6 | SHOULD FIX | Measure actual database acquisition without forcing it | Avoid both missing waits and instrumentation-induced work |
| F7 | SHOULD FIX | Remove initialization behavior keyed to audit actor | Keep production and journey initialization consistent |
| F8 | SHOULD FIX | Add usefulness and false-refusal acceptance coverage | Make the balance measurable and prevent overcorrection |

F1–F7 are findings from the code review. F8 is an acceptance/refinement recommendation, not a claim that every scenario currently fails. Complete F1–F4 before merge. F5–F6 are also necessary before trusting latency conclusions. Keep F7 small and complete F8 alongside the functional fixes.

## Detailed implementation guidance

### F1 Preserve applicability under document scope

**Where:** `backend/app/composition/source_metadata.py`, `capture()` around line 38; `backend/app/modules/retrieval/source_policy.py`, `_candidates_for_enforcement()` around line 208; normal and exact-recall callers in `modules/retrieval/services/search_service.py`.

**Evidence and impact:** Scope capture disables the enforcement join when a document ID is present. The post-ranking helper then changes matching candidates from `source_policy_applicable=False` to `True` and removes the exclusion reason, regardless of reason. This is broader than its current-replacement comment. Source metadata defines exclusions such as `draft`, `outside_effective_interval`, `retired_replaced`, and `source_replaced`. The approved plan explicitly preserves source-policy constraints.

**What to change:** Remove the blanket override and keep document filtering orthogonal to applicability. Both normal search and exact identity recall must enforce the same current source-policy contract. Preserve exclusion metadata for diagnostics instead of rewriting it to imply eligibility.

**Approach:** First add failing policy tests and PostgreSQL integration cases. Trace scope capture, candidate filtering, hydration, and exact recall together so no second path reintroduces excluded content. Restore the smallest behavior consistent with the approved plan; do not add a new configuration flag to retain the bypass.

**Usability:** When the selected document cannot answer a current-rule question, explain the specific limitation. Use existing historical scope where the user actually supplies a historical intent/date. A general “inspect obsolete or draft content” mode is a separate product feature requiring explicit semantics; do not smuggle it into a document filter or automatically change the user's date.

**Verification:** Test scoped draft, future-effective, expired, replaced, valid current, and valid historical documents in normal and exact recall. Assert results, exclusion reasons, metadata, and historical web suppression. Positive controls must prove eligible scoped documents remain usable.

### F2 Separate shortcut rejection from answer rejection

**Where:** `modules/conversations/services/rewrite_retrieval.py`, `rewrite_followup_mode()`, `_residual_factual_request()`, `retained_rewrite_question()`, and `try_presentation_preflight()`; `turn_resolver.py`; `ChatService._prepare_turn()` around lines 830–925.

**Evidence and impact:** The residual check returns true only for two or more unexplained tokens. Probes classify “Summarize penalties,” “Rewrite it for minors,” and “Summarize it for partnerships” as presentation-only. That can retain the old factual query and skip retrieval/recovery for the changed request.

**What to change:** Admit only clearly recognized fact-preserving transformations to deterministic preflight. Recognize supported formatting and language variants without treating short length as evidence of unchanged facts. Unknown wording should use the existing resolver once. Do not override a resolver decision establishing changed facts or scope with a weaker heuristic later in `_prepare_turn()`.

**Approach:** Return to the existing resolution flow when deterministic eligibility is unknown. Keep the current follow-up enums and routing machinery; a large new intent framework is unnecessary. Consolidate the eligibility predicate used by preflight and retained-topic scanning so they cannot disagree. Preserve an explicit factual query across successive valid rewrites.

**Usability:** A failed shortcut should normally cost resolution/retrieval, not produce “insufficient evidence.” Audience simplification can still be accepted by the resolver. Ask a clarification only when materially different interpretations remain after using conversation context. Do not replace the two-token rule with a universal one-token rejection that bypasses this normal route.

**Verification:** Pair unsafe cases with “explain that simply,” “for beginners,” “three bullets,” translation, and Bangla transformations. Assert routing mode, retained query, resolver call count, retrieval path, language, and citations. Test ambiguous audiences and factual populations. Invalid citation provenance should trigger scoped fallback, not mandatory reclassification or automatic refusal.

### F3 Verify meaning without making lexical weakness a veto

**Where:** `modules/conversations/grounding_service.py`, `_suppress_negation_mismatch()`, `_bounded_entailment_guard()`, `_aligned_entailment_clause()`, and `map_claims()`; `tests/unit/modules/conversations/test_grounding_service.py`.

**Evidence and impact:** With evidence “The certificate is not valid unless the prescribed fee is paid,” `map_claims()` marks “The certificate is valid only if the prescribed fee is not paid” as supported using lexical verification. A necessary-condition pattern on both sides does not establish matching polarity.

**What to change:** Compare the main proposition and required condition separately. Track explicit negation in each clause and distinguish necessary from sufficient conditions. Suppress superficial negation mismatch only when the constructions actually align. A conservative bounded helper is sufficient; do not introduce an entailment model or another mandatory LLM call.

**Approach:** Add minimal negative pairs first. Reuse existing clause alignment. When grammar is too ambiguous for the bounded rules, return an inconclusive verification result rather than declaring a contradiction or allowing a known hazardous construction to pass through lexical similarity as supported. Keep existing statuses and thresholds. Do not remove unrelated quantity and authority checks.

**Usability:** The current code separates pre-generation `blocks_generation()` from post-generation `map_claims()`; an unverified claim is not automatically a refusal. Preserve that distinction. A faithful paraphrase should remain supported. An uncertain clause should not erase independently supported content or be mislabeled contradicted. In authoritative output, do not present a known reversed condition as established merely with a generic disclaimer.

**Streaming constraint:** Post-generation verification occurs after tokens may already have been delivered. Do not promise that changing claim labels prevents exposure to every erroneous streamed sentence. Keep honest final metadata. Any future automatic answer-repair or buffering feature needs its own delivery contract, tests, and latency budget; it is not part of this fix.

**Verification:** Positive necessary-condition paraphrases; negation in the main clause, condition, or both; sufficient-condition reversals; unrelated negative clauses; multiple conditions; English/Bangla controls. Assert claim status/reason, preservation of other claims, unchanged provider calls, and no strict/balanced admission regression.

### F4 Keep gaps until evidence resolves them

**Where:** `modules/conversations/services/evidence_repair_service.py`, `_TurnProofMap.merge_delta()` around line 431, `canonical_verdict()`, `snapshot_verdict()`, `_gap_resolved()`, and `_handoff_reviewed_proof()`; `services/evidence_coverage.py`.

**Evidence and impact:** Retain R1 proof and an additional missing penalty schedule, then merge a delta proving R2 with `missing=[]`. The merge returns complete with no gaps, while a snapshot of the same map remains incomplete. The snapshot remediation did not fix ordinary delta reconciliation.

**What to change:** Use one reconciliation path for retained gaps, newly reported gaps, canonical requirements, and accepted partial scope. Delta omission is not resolution. Close a gap only through explicit validated proof or a validated determination that it is outside the user's requested scope. Avoid fuzzy label overlap as the sole reason to discharge an obligation.

**Approach:** Prefer the existing canonical requirement IDs where possible. Preserve unmatched gaps explicitly rather than forcing them into an unrelated ID. Bind closure to evidence/checks and invalidate it when its dependencies change. Share a small pure reconciliation helper between merge and snapshot, keeping turn-local ownership. Do not introduce a durable proof graph.

**Usability:** Retaining a gap must not force refusal of independent supported facets. Pass the accepted partial scope through `_handoff_reviewed_proof()`, `_prepare_turn()`, and `PromptBuilder.build()`. Explain supported results first and unresolved facets once. A missing dependency of a combined amount blocks that amount, not every explanatory rule.

**Verification:** Reproduce the merge/snapshot disagreement in a unit test and a two-round service test. Assert identical completeness semantics, no silent gap deletion, partial scope in the prompt, and persistence across rewrites. Test explicit resolution, similar labels, changed authority, timeout/cancellation, and dependent versus independent subtotals.

### F5 Make streaming context ownership explicit

**Where:** `ChatService.stream_message()` around line 451; `api/v1/routes/conversations_router.py`, `_with_sse_heartbeats()`; `platform/providers/request_work.py`, `attached()` and `_reset_contextvar()`.

**Evidence and impact:** The router advances the generator in a new task for each event. The service binds its ContextVar only upon initial entry. A reproduction using that pattern observes request work on event one but not events two or three. Preparation begins after the initial progress yield. Swallowing token-reset errors does not propagate context.

**What to change:** Give the stream one clear owner. Prefer one producer task that owns generator advancement and closure, while heartbeat delivery consumes its events. A bounded queue must propagate backpressure and cancellation. Alternatively, bind and reset context around each advancement in the task actually consuming it; never hold a token across yields consumed in another context.

**Approach:** Choose one design, not both. Explicitly close nested generators, await cancelled preparation/provider tasks, and preserve error propagation. Do not introduce shared global current-request state. Keep existing session ownership and release-before-provider-call behavior.

**Verification:** Test through the actual heartbeat wrapper, including delayed tokens, cancellation during preparation/generation, provider failure, and concurrent conversations. Assert work identity, span/lease/DB attribution, prompt cleanup, and no surviving tasks. Heartbeats must remain responsive without unbounded buffering.

### F6 Observe database acquisition without altering it

**Where:** `platform/db/session.py`, `ObservedAsyncSession.connection()`, `execute()`, and `flush()`; `tests/unit/platform/test_observed_session.py`.

**Evidence and impact:** `in_transaction()` can be true after logical autobegin without a checked-out connection, so acquisition may go unmeasured. Conversely, the override forces acquisition for an empty flush. Existing tests mock the transaction predicates as false and cannot detect either behavior.

**What to change:** Measure the existing connection acquisition boundary without treating a logical transaction as a connection. Preserve deferred checkout and SQLAlchemy bind arguments. Avoid instrumenting by forcing extra database work. Keep the label `database_connection_acquisition`; it is not pure pool waiting.

**Approach:** Use supported engine/session facilities or narrow explicit boundaries already requiring a connection. Validate the instrumentation against a real pool before broadening overrides. Do not access unstable private internals merely to satisfy a counter test. When an acquisition cannot be observed reliably, report the limitation rather than manufacturing a zero-duration measurement.

**Verification:** Add→flush, explicit begin→query, execute/scalar/get, repeated queries, empty flush, failure, cancellation, pool saturation, and relevant bind configuration. Compare checkout counts and transaction behavior with instrumentation absent. Existing release-before-provider-call tests must continue to pass.

### F7 Remove audit-identity-dependent project behavior

**Where:** `modules/projects/services/project_service.py`, `create()` around line 117; `cli/rag_journey.py`, `run_journey()` baseline activation.

**Evidence and impact:** The literal actor `rag-journey` skips the initial factual configuration revision. Tests and journeys therefore exercise a different initialization path from normal project creation.

**What to change:** Create the standard initial revision regardless of audit actor. Let the journey activate its desired baseline using the current revision ID, which the CLI already reloads. Keep audit identity descriptive.

**Verification:** Compare initialization across normal, admin, and journey actors. Verify revision ownership, optimistic-concurrency behavior, and journey cleanup/activation. Remove tests that require the magic-actor exception rather than preserving product behavior.

### F8 Make practical usefulness part of acceptance

**Where:** Existing conversation/rewrite/recovery/grounding tests; `PromptBuilder.build()`; `_prepare_turn()` and `_insufficient_content()`; frontend `features/lab/TestLab.tsx`; the existing journey/evaluation tooling.

**Status:** This is a refinement recommendation. Do not assume all these paths are broken or implement a replacement policy layer.

**What to change:** Add paired evaluations for helpful supported answers and unsafe near-neighbors. Preserve existing supported partial-answer handling. If a new test reveals unnecessary whole-answer refusal, fix the narrow handoff or dependency decision instead of weakening `UNRESOLVED_AUTHORITY` globally.

**Approach:** Use existing `EvidenceDecision`, answerable scope, partial scope, and per-claim statuses. Keep module responsibilities clear: retrieval supplies eligible evidence; recovery tracks proof and dependencies; conversation orchestration applies response mode; prompts express scope; grounding records support; UI explains it. Avoid parallel “confidence” booleans that duplicate these meanings.

**Prompt behavior:** Lead with the supported answer. Follow with a concise, specific limitation. Preserve language and presentation requests. Ask only for user-provided facts that materially affect the result; missing source evidence is not necessarily something the user can answer. Do not repeat a boilerplate warning for each bullet or represent “not found in retrieved evidence” as “not in the corpus.”

**UI behavior:** Test Lab currently treats partial answers as failing its complete-answer success condition. Keep that distinction, but add a separate useful-partial outcome in evaluation/reporting if needed. Do not inflate the existing complete-answer pass rate. Supported partial output, clarification, honest abstention, and erroneous answers have different meanings. This reporting refinement should not change public API semantics without compatibility tests.

**Response modes:** Preserve `indexed_only`, `indexed_then_web`, and `indexed_and_web` behavior. A partial answer is not automatic permission to search the web. Scoped/historical requests and unresolved-authority suppression retain their current rules. General background from model memory is not an approved fallback for missing indexed evidence.

**Verification:** Exercise the scenario table through preparation, generation prompts, final metadata, and UI where applicable. For each refusal case add an answerable near-neighbor. For each accepted paraphrase add a meaning-changing negative. Use human review for usefulness and naturalness; an LLM judge may assist offline, but should not be the only merge gate.

## What to preserve

Keep exact indexed identity lookup, separate raw and span hashes, current authority rechecks, inherited scope, scoped fallback, and final claim verification. Keep validated partial-answer propagation, bounded selector retries, proof retention, structured authority comparisons, and duration equivalence. Keep separate first-token, stream-completion, and message-refresh timing.

Do not globally lower grounding thresholds, switch authoritative behavior to observe, treat every rewrite as trusted, or add mandatory review calls to every turn. These would change the product contract and could undo the latency work.

## Evaluation and completion gates

Run existing regression suites after each bounded fix; add tests that reproduce the defect before changing behavior. Then run the full CI pipeline and PostgreSQL integration suite at the final commit. The previous passing results do not certify future edits.

Use a fixed, human-labeled set spanning the scenario table, including multilingual and multi-turn cases. Track these separately, with denominators and case IDs:

- Unsupported assertions marked supported, and invalid authority accepted.
- Answerable requests unnecessarily refused or unnecessarily clarified.
- Independently supported facets actually delivered to the user.
- Useful partial answers versus correctly complete answers.
- Citation correctness, current/historical applicability, and constraint retention across turns.
- Resolver, retrieval, review, and verification calls/tokens by purpose.
- Server elapsed, client first answer token, done, stream close, and message refresh.

Acceptance is not a weighted score that allows more unsupported claims in exchange for fewer refusals. All reproduced negative controls must pass; paired positive cases must remain useful. Investigate any increased false-refusal rate before merging. Define corpus-specific quantitative targets from the baseline instead of inventing a universal threshold.

For latency, follow the approved plan: fixed prompts/configuration/corpus/provider settings; at least three repetitions per relevant case; fresh conversations plus the exact three-turn rewrite chain; individual results and median/range. Three repetitions do not establish reliable p95. Fix F5–F6 before attributing latency to particular stages.

Validate real browser SSE timing, disconnect cleanup, and Redis saturation/cancelled waiters. CI's availability of PostgreSQL/Redis does not demonstrate load behavior. Record one successful and one fallback/failure trace for each affected stage. If live testing cannot run, state the missing evidence and leave performance claims unproven.

## Implementation order and scope control

1. Add reproductions and paired positive tests for F1–F4; implement the smallest fixes.
2. Add F8 partial-answer and conversational tests alongside those fixes, so conservatism does not become unnecessary refusal.
3. Fix F5–F6 and validate lifecycle and timing through real consumption/acquisition paths.
4. Remove the small F7 special case and verify journey behavior.
5. Run full regression/integration, live acceptance cases, and benchmark comparisons.
6. Update the PR description with final behavior, compatibility impact, test commands/results, traces, measured calls removed or retained, limitations, and a rollback reference.

DEFER provider/reranker selection, infrastructure resizing, cross-turn caches, durable facet graphs, new entailment models, autonomous multi-agent orchestration, general obsolete-document inspection, and automatic post-stream answer repair. Investigate them separately only when measurements or product requirements justify them.

## Instructions for the implementing model

Use this review together with the approved execution plan. Confirm that the target commit still contains each issue; line numbers are approximate and functions are the stable reference. If the code has moved, trace callers and tests before editing. Preserve unrelated user changes.

Implement each fix with its negative reproduction and positive usability controls. Prefer existing types and module boundaries. Do not add provider calls, change source/web policy, or convert uncertainty into blanket refusal to make a test pass. Distinguish a failed optimization from a failed answer. Report any conflict with the approved plan rather than silently expanding scope.

The final handoff should say exactly what changed, why, what tests and live checks actually ran, which useful-answer cases remained intact, and which acceptance evidence is still missing. Do not claim the PR is production-ready solely because CI is green.
