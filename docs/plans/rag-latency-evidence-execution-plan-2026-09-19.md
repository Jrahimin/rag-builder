# RAG latency and evidence handling: engineering execution plan

## Baseline, decisions and delivery boundaries

Implement against commit `a58534c`, starting from `E:\python-projects\rag-builder`. Deliver each numbered change independently; do not combine latency, response-policy, and grounding changes into one patch.

### Findings that constrain implementation

- Citation recall already exists, but calls ranked retrieval through `SearchService.search(cited_chunk_ids=...)`.
- Presentation detection currently happens after the resolver. Normal evidence assessment also runs before exact-recall overrides it.
- `bypass_resolution()` creates a standalone turn; it cannot be reused unchanged to represent a presentation follow-up.
- Validated partial recovery currently produces `EvidenceDecision(sufficient=True)`. Consequently, some partial answers already suppress `indexed_then_web` fallback.
- Recovery already has query ownership, bounded parallel searches, partial checkpoints, unchanged-evidence detection, and turn-local embedding/content caches.
- Duration equivalence, coverage-scope verification, and some clause alignment already exist. Stage 3 extends them rather than replacing them.
- Middleware already records response-header readiness and server body completion. Reuse those measurements.
- The frontend's elapsed measurement includes fetching persisted messages after streaming completes. That is not solely answer latency.

### Invariants for every stage

1. Do not add a mandatory LLM classifier, planner, or final audit.
2. Do not lower evidence thresholds or change strict, balanced, or observe semantics.
3. Keep web acquisition owned by `ChatService`.
4. Do not add SSE event types.
5. Do not add a durable facet graph, database migration, or cross-conversation evidence cache.
6. Keep final claim verification enabled, including for transformations.
7. Preserve existing streaming behavior: claim verification occurs before persistence, but streamed text can already have reached the client.
8. Do not change provider selection, reranker settings, concurrency limits, or server size in these stages.
9. Use separate commits and deployments for rollback; do not add operator configuration controls merely to ship these changes.

---

## Stage 1A — instrument the existing critical path

### Files and functions

| File | Functions/areas |
| --- | --- |
| `backend/app/platform/providers/request_work.py` | `RequestWork.stage`, `snapshot`, `ObservedLLM._call`, embedding-call recording |
| `backend/app/modules/conversations/services/chat_service.py` | `send_message`, `stream_message`, `_prepare_turn`, `_persist_assistant_turn`, `_preparation_progress` |
| `backend/app/dependencies/conversations.py` | `get_chat_service`, `SearchServiceRetrievalAdapter.retrieve_batch`, `_deployment_slot` |
| `backend/app/platform/infra/recovery_capacity.py` | `recovery_slot` |
| `backend/app/platform/db/session.py` | Connection-acquisition instrumentation |
| `backend/app/modules/retrieval/retrievers/hybrid_retriever.py` | Existing reranker-call recording |
| `backend/app/core/middleware.py` | Existing `request_headers_ready` and measured-body completion logs |
| `frontend/src/api/operatorApiClient.ts` | `streamMessage` |
| `frontend/src/api/operatorConsoleQueries.ts` | `useStreamMessage` |
| `frontend/src/features/lab/TestLab.tsx` | Message submission, elapsed measurements, message diagnostics display |

### Current behavior

`RequestWork` stores aggregate durations and provider calls, but overlapping work is difficult to reconstruct. LLM records do not identify their purpose. Progress labels infer activity from cumulative call counts.

The persisted lifecycle snapshot is taken before the assistant-message commit. The later server log contains persistence timing. Client elapsed time additionally includes the post-stream message fetch and UI work.

### Implementation order

#### 1A.1 — Add additive span and purpose metadata

Retain existing `lifecycle.version`, `stages_ms`, `counts`, `provider_calls`, and `validation_retries`.

Add an optional, versioned span collection containing:

- Span ID and parent ID.
- Purpose/stage.
- Start offset and elapsed milliseconds.
- Completed, failed, or cancelled outcome.
- Sanitized error/retry category.
- Provider-call association where applicable.

Use task-local context for parent and purpose propagation so parallel recovery branches do not overwrite one another. Bound retained span detail; expose truncation explicitly while retaining aggregate counts.

Do not store prompts, source text, credentials, or raw provider exceptions.

#### 1A.2 — Label existing provider calls

Set purpose around existing call sites:

- Turn resolution.
- Recovery planning.
- Coverage review.
- Scenario-input review.
- Structured-response retry.
- Selector retry.
- Web-evidence review.
- Answer generation.
- Claim-verification embeddings.

Keep provider attempt counts distinct from SDK-internal retries. Preserve unknown retry values when the provider does not expose them.

Add independent counters for ranked retrieval, resolver calls, planner calls, coverage-review calls, and recovery attempts. These support Stage 1B assertions; do not infer them from total LLM calls.

#### 1A.3 — Measure waits without changing scheduling

Measure acquisition time separately for:

- Per-batch recovery semaphore.
- Process recovery semaphore.
- Deployment Redis lease.
- Database connection acquisition.

End each wait span immediately after acquisition; do not include the protected operation.

Measure database acquisition only at boundaries that already need a connection. Label it `database_connection_acquisition`, because it can include pool waiting, connection creation, and pre-ping. Do not describe checkout-event duration as pure pool wait.

Keep semaphore sizes, Redis behavior, session ownership, and release-before-provider-call behavior unchanged.

#### 1A.4 — Separate answer delivery from post-stream work

Use the existing request ID and middleware completion logs.

Add optional client-side timing data for:

- Request initiation.
- Response headers.
- First nonempty answer token.
- `done` receipt.
- Stream closure.
- Persisted-message fetch completion.

Keep `useStreamMessage`'s existing post-stream fetch and result behavior. Pass timing data through an optional client callback or result extension; existing callers must remain valid.

The operator UI should distinguish server processing, first token, stream completion, and message refresh. Do not label first token “first useful answer.”

#### 1A.5 — Replace guessed progress labels

Derive progress from active execution phases rather than LLM/reranker count thresholds. Continue sending existing `progress` events.

Use concise labels such as “Finding cited passages,” “Finding relevant sources,” “Checking missing details,” “Checking source applicability,” and “Preparing your answer.” Do not expose internal prompts or evidence text.

### Compatibility and tests

No migration and no required API fields. Old lifecycle records must still render.

Update:

- `tests/unit/platform/test_request_work.py`: nested/parallel spans, cancellation, failures, purpose attribution, and bounded detail.
- `tests/unit/modules/conversations/test_batch_retrieval.py`: separate waits, unchanged concurrency, snapshot checks, and sibling cancellation.
- `frontend/src/api/operatorApiClient.test.ts`: token versus done versus EOF timing, cancellation, and premature EOF.
- `frontend/src/features/lab/TestLab.test.tsx`: timing labels and legacy diagnostics.

### Acceptance

- Instrumentation adds **zero provider calls**.
- Existing successful, failed, and cancelled outcomes remain unchanged.
- Parallel durations are not presented as additive total latency.
- Final server logs include persistence; persisted metadata is not misrepresented as including it.
- Client refresh time is distinguishable from stream-delivery time.
- Existing streaming clients pass unchanged compatibility tests.

---

## Stage 1B — refactor citation recall into validated presentation reuse

### Files and functions

| File | Functions/areas |
| --- | --- |
| `backend/app/modules/conversations/services/rewrite_retrieval.py` | `rewrite_followup_mode`, `retained_rewrite_question`, `used_citation_items`, `rewrite_citation_ids`, `retrieve_rewrite_context` |
| `backend/app/modules/conversations/turn_resolver.py` | `ResolvedTurn`, `bypass_resolution`, diagnostics construction |
| `backend/app/modules/conversations/turn_resolution.py` | Existing follow-up types, validation, effective scope construction |
| `backend/app/modules/conversations/ports.py` | `RetrievalPort`, `ContextRetrievalResult`, `EvidenceUnit` |
| `backend/app/modules/retrieval/services/search_service.py` | Snapshot/source-scope capture and provenance assembly |
| `backend/app/modules/retrieval/repositories/retrieval_chunk_repository.py` | Exact indexed identity lookup |
| `backend/app/modules/retrieval/repositories/chunk_keyword_index_repository.py` | Existing index membership and metadata filtering |
| `backend/app/modules/retrieval/retrievers/result_hydrator.py` | `ResultHydrator.hydrate` |
| `backend/app/modules/retrieval/source_policy.py` | Source snapshot, provenance, and policy helpers |
| `backend/app/modules/conversations/grounded_context.py` | `select_exact_recalled_knowledge` and ordinary admission boundary |
| `backend/app/modules/conversations/citation_snapshots.py` | `build_citation_snapshots` |
| `backend/app/modules/conversations/schemas/message.py` | Optional citation provenance fields |

Also change the Stage 1A `ChatService` and dependency-adapter files.

### Current behavior being replaced

The current sequence is:

`Resolver → rewrite detection → ranked citation search → ordinary assessment → exact-recall override → generation → verification`

Replace that sequence only when deterministic routing and evidence validation establish eligibility.

### Implementation order

#### 1B.1 — Add characterization tests before changing routing

Cover “Do not add new facts,” successive rewrites, Bangla wording, explicit additions, and changed scopes. Assert the exact `FollowupMode`, not merely retained citation IDs.

Capture existing fallback behavior for missing citations and adapters without exact-recall support.

#### 1B.2 — Persist sufficient provenance for future reuse

Extend existing citation/message JSON, without new tables.

Record optional, versioned evidence provenance sufficient to distinguish:

- Raw indexed chunk identity/hash.
- Source text after any authority redaction.
- Exact admitted span hash and local offsets.
- Derivation/corroboration information.
- Effective configuration hash and snapshot ID.
- Index build, metadata generation, source revision, and processing version.
- Effective document/metadata/as-of scope.
- Existing validated coverage/partial-scope diagnostics and their originating message.

`EvidenceUnit.chunk_hash` is a span hash, and `evidence_source_chunk_hash` may refer to authority-redacted content. Do not reinterpret either as the raw stored chunk hash. Preserve the raw hash before redaction and name the fields distinctly.

For reconstructed table/header context, reproduce the existing derivation or decline fast reuse. Do not pretend its document envelope is a contiguous evidence slice.

Legacy messages remain readable. Missing provenance makes them ineligible for unchecked reuse, not invalid conversations.

#### 1B.3 — Add deterministic presentation preflight

Run before `TurnResolver.resolve()`.

Eligibility requires:

- An identifiable preceding assistant answer.
- A clear transformation request.
- No requested factual expansion, changed applicability, comparison, calculation, or current-data update.
- Sufficient saved context to establish the retained topic and effective scope.

Construct a valid `ResolvedTurn` with `RESOLVED`, `FOLLOW_UP`, and `PRESENTATION_ONLY`, using existing validation and scope machinery. Do not call the standalone-only `bypass_resolution()` unchanged.

Unknown intent uses the existing resolver once. A failed evidence validation does not itself require an LLM to reclassify an otherwise clear transformation.

Preserve explicit request filters. A supplied scope differing from saved evidence scope exits fast reuse. An unchanged inherited historical scope must remain historical and web-suppressed.

#### 1B.4 — Extend the existing adapter with exact recall

Add an explicit optional exact-recall operation to the existing retrieval adapter/port. Keep `retrieve(cited_chunk_ids=...)` for legacy callers and fallback.

Implement the exact operation using shared snapshot capture, indexed repository lookup, source-policy filtering, and hydration. It must not call the ranked retriever, query translator, reranker, or passage scorer.

Extract shared snapshot/policy preparation from `SearchService` where necessary; do not build another search service or duplicate policy rules.

Use only bounded citation identities from the prior visible answer, plus already-recorded authority dependencies where required. Reading current relationship metadata is allowed. Discovering a new modifier or needing unrecorded evidence exits the fast path.

An empty identity restriction must never become an unrestricted query.

#### 1B.5 — Validate and reconstruct evidence

Against one pinned snapshot:

1. Verify project/access, document/version, and active-index membership.
2. Apply existing source-policy and request-scope constraints.
3. Recheck authority dependencies and effective dates.
4. Reconstruct the same authority-safe text and evidence span.
5. Verify hashes and offsets.
6. Rebuild `EvidenceUnit` objects.
7. Apply existing context budgeting and authority checks.

Use `EvidenceUnit`, not plain `ContextChunk`, to preserve indivisible-span budgeting. If budgeting removes required proof, do not report complete reuse.

An unrelated Project revision does not invalidate a saved conversation configuration. A changed effective configuration conservatively requires revalidation. An index/metadata generation change requires revalidation, but unchanged eligible evidence may survive.

Retain prior uncertainty. Admission reuse is not approval of every prior or transformed claim.

#### 1B.6 — Branch before normal assessment

A successful fast path bypasses `assess_and_select_knowledge()` and `repair_knowledge_evidence()`. It then joins normal prompt construction, generation, claim verification, and persistence.

Carry applicable validated partial-scope information without turning it into full coverage. Ensure diagnostics report the current method as exact citation recall even when prior coverage diagnostics are inherited.

Invalid or missing evidence follows the existing scoped fallback, with a reason recorded. Stage 1 does not implement partial-facet salvage for an otherwise invalid transformation.

### Contract and diagnostics

Keep existing fields and add:

- Routing origin: deterministic, resolver, or fallback.
- Reuse validation version/outcome.
- Reuse failure category.
- Originating assistant-message identity.
- Reused evidence count and bounded invalidated identities.
- Inherited coverage provenance, distinct from a new review.

Required success values:

- `knowledge_repair.status = not_needed`
- `coverage_method = current_exact_citation_recall`
- `admitted_passages = 0`
- Accurate `reused_cited_passages`

Citation numbering may change to match the new answer’s ordering; source attribution must remain correct.

### Response-mode boundary

For `indexed_only`, and eligible `indexed_then_web` turns whose existing indexed policy permits the answer:

`Deterministic routing → exact validation/hydration → transformation → claim verification → persistence`

For `indexed_and_web`, preserve the combined web workflow. Its indexed branch can use exact recall, but the whole turn does not receive the no-web guarantee.

Do not silently omit prior web citations to qualify a mixed-source answer for indexed-only reuse.

### Tests and acceptance

Update:

- `tests/unit/modules/conversations/test_rewrite_retrieval.py`
- `tests/unit/modules/conversations/test_turn_resolver.py`
- `tests/unit/modules/conversations/test_chat_service.py`
- `tests/unit/modules/conversations/test_citation_snapshots.py`
- `tests/unit/modules/retrieval/test_search_service.py`
- `tests/unit/modules/retrieval/test_result_hydrator.py`
- `tests/unit/modules/conversations/test_context_builder.py`

Add repository integration coverage alongside `tests/integration/test_phase3_source_retrieval.py`.

For unquestionably eligible presentation turns, assert:

| Operation | Required count |
| --- | ---: |
| Resolver LLM | 0 |
| Ranked retrieval | 0 |
| Query-translation LLM | 0 |
| Reranker/passage rescue | 0 |
| Coverage planner/reviewer | 0 |
| Recovery attempts | 0 |
| Requested transformation | One logical generation operation |
| Final claim verification | Runs normally |

Test three bullets, English output, shorter second rewrite, supported facts, and citations. Include configuration/index/document/access/authority changes, historical scope, context overflow, missing provenance, and mixed web/indexed citations.

Expected improvement: remove resolver and ranked-recall overhead from valid transformations. Do not promise a fixed latency percentage.

---

## Stage 2 — preserve policy while making recovery incremental

### Files and functions

| File | Functions/areas |
| --- | --- |
| `backend/app/modules/conversations/services/evidence_repair_service.py` | `EvidenceRepairResult`, `_SearchPlan`, `_prepare_search_plan`, `repair_knowledge_evidence`, `_review_evidence_key`, `_checkpoint_matches_branch`, `_restore_partial_checkpoint`, `_handoff_reviewed_proof`, `_validated_completion`, `_structured_selector_retry` |
| `backend/app/modules/conversations/services/evidence_coverage.py` | `CoverageVerdict`, `resolve_source_ranges`, `validates`, `partial_validates` |
| `backend/app/modules/conversations/prompts/authoritative_compatibility.py` | Authoritative planning/review prompt contracts |
| `backend/app/modules/conversations/prompts/evidence_coverage.py` | Coverage-review instructions |
| `backend/app/modules/conversations/prompt_builder.py` | Partial-scope generation instructions |

Also update `ChatService._prepare_turn`, diagnostics assembly, and existing recovery tests.

### Implementation order

#### 2.1 — Characterize and separate policy outcomes

Before changing recovery, add a response-mode matrix for:

- Complete indexed evidence.
- Currently accepted partial evidence.
- Incomplete evidence without accepted partial scope.
- Unresolved authority.
- Scoped requests.
- Web success, rejection, failure, and unavailability.
- Enforce and observe modes.

Introduce a small internal distinction between:

- Indexed policy decision used by response-mode routing.
- Validated answerable scope and its unresolved facets.

Initially populate both from current behavior. Preserve `EvidenceDecision` compatibility for existing consumers.

Do not redefine fallback as `not coverage.complete`. Current validated partials already qualify as sufficient. Do not freeze the pre-recovery decision either: successful recovery must still suppress unnecessary fallback.

**Stage 2 does not broaden partial-proof eligibility.** Continue requiring existing `partial_validates()` guarantees. Improve how proof reaches that validator and how the answer presents it. Any future relaxation needs a separate policy change.

#### 2.2 — Build an in-turn proof map

Use a local map keyed by existing requirement IDs. Each entry contains:

- The validated check.
- Referenced evidence-unit identities/hashes.
- Relevant authority dependencies.
- Requirement description and origin.
- Current validity state.

Keep it inside the existing recovery execution/checkpoint structures. Persist only bounded diagnostics through message JSON. Do not reconstruct a cross-conversation graph.

Preserve the canonical requirement list. A later review cannot rename an obligation to change its meaning or remove an unresolved requested obligation.

#### 2.3 — Reuse initial proof in the existing planning exchange

The non-authoritative path already accepts initial coverage; the authoritative compatibility path intentionally skips it.

Extend the authoritative planning payload/prompt to optionally return `coverage` using existing `_SearchPlan.coverage`. Supply only admitted, authority-safe evidence with existing source selectors.

Validate returned proof through current range, quote, authority, and completeness checks. If coverage is omitted or invalid, retain the existing search behavior. Do not add a separate initial-review call.

Initially proven facets populate the in-turn proof map. Filter queries only when all their owned requirements are already proven. Mixed-ownership queries remain eligible.

Keep authoritative and semantic review protocols distinct; do not substitute one prompt for the other.

#### 2.4 — Review only changed dependencies

After each search batch:

1. Validate the pinned snapshot as today.
2. Admit evidence through the existing machinery.
3. Compare new evidence and authority records against proof dependencies.
4. Retain unaffected validated checks.
5. Invalidate changed facets and their dependents.
6. Review unresolved/changed facets with their necessary evidence.
7. Merge the result into a complete canonical verdict containing all original requirement IDs.
8. Run existing full/partial validators before handoff.

Use a narrow internal delta-response schema rather than weakening `CoverageVerdict` to accept missing original checks.

Replace substring searches over serialized authority records with structured identity/scope comparisons. Repeated identical records must not invalidate proof. Unknown relationship scope remains conservative.

Keep existing query ownership, optional-corroboration filtering, adjacency logic, parallel batch retrieval, caches, and budgets.

#### 2.5 — Extend no-progress stopping

The review key must include relevant evidence hashes, authority dependencies, and unresolved requirement state.

Do not repeat a review solely because rank, query wording, or unrelated candidates changed. New authority information can reopen affected proof even when source text is unchanged.

Restore only a checkpoint whose dependencies remain valid. Preserve unresolved facets and stop reasons; do not mark partial coverage complete.

#### 2.6 — Narrow selector retries

The existing `_structured_selector_retry()` rewrites context representation but still retries the review. Extend it selectively:

- Canonicalize known source aliases and schema-supported equivalent selector forms locally.
- For otherwise valid results with isolated selector failures, request only replacement selectors for identified checks.
- Apply replacements to those selectors only, then validate the entire result.
- Keep the existing total retry bound.

Do not guess source identities, clamp ranges, choose neighboring text, salvage truncated JSON, or repair contradictory completion claims. Semantic/schema contradictions keep the existing bounded full retry or checkpoint fallback.

#### 2.7 — Generate useful partial answers

Use the existing validated scope, missing facets, and source proof in `PromptBuilder.build()`.

Lead with independently supported content. Follow with concise unresolved facets. Preserve applicability conditions and uncertainty.

Coverage limitations describe selected evidence, not corpus-wide absence. Do not add another broad audit or post-generation rewrite call.

### Response-mode acceptance

- `indexed_only`: validated supported subset can be returned with explicit unresolved facets.
- `indexed_then_web`: existing policy decision controls fallback; formatting a partial answer cannot alter it.
- `indexed_and_web`: preserve combined acquisition and selection.
- Document/metadata/as-of restrictions and unresolved-authority suppression remain unchanged.
- `EvidenceRepairService` never creates a web provider or invokes web search.

### Compatibility and tests

No migration. Keep existing `EvidenceRepairResult` construction compatible through optional/defaulted additions. Preserve legacy query-string parsing, diagnostic fields, and successful full-verdict behavior.

Extend `tests/unit/modules/conversations/test_evidence_repair_service.py` and the existing chat/batch suites with:

- Initial evidence resolving all or some requirements.
- No extra initial LLM call.
- One facet changing while another remains valid.
- Shared dependencies invalidating every dependent facet.
- Identical authority records causing no invalidation.
- New scoped authority reopening only affected work.
- Unknown authority scope remaining conservative.
- Malformed selector-only retry versus contradictory-verdict retry.
- Timeout/cancellation restoring only valid proof.
- Requirement descriptions and unresolved obligations surviving delta merges.
- Full response-mode matrix in streaming and non-streaming execution.

Expected improvement: fewer repeated searches/reviews and smaller review inputs. Deterministic tests must prove call-count reductions in the relevant fixtures before provider benchmarks.

---

## Stage 3 — targeted grounding and authority corrections

### Files and functions

| File | Functions/areas |
| --- | --- |
| `backend/app/modules/conversations/grounding_service.py` | `map_claims`, `_answer_segments`, `_verification_context`, `_coverage_kind_hint`, `_coverage_scope_verification`, `_best_evidence_spans`, `_duration_quantities`, `_durations_equivalent`, `_bounded_entailment_guard`, `_aligned_entailment_clause` |
| `backend/app/modules/conversations/current_authority.py` | `remove_superseded_provisions`, `annotate_authority_limitations`, `_explicitly_disjoint_provisions`, `cited_authority_summary` |

Reuse existing source-selection and coverage validators. Do not alter admission thresholds.

### Implementation order

#### 3.1 — Add paired failing fixtures first

Use sanitized recorded examples plus negative controls. Several requested behaviors already pass: 21 days versus three weeks, Bangla scope bullets, genuine duration mismatch, and unrelated negative clauses. Keep these as regression anchors.

Change implementation only for a demonstrated failure.

#### 3.2 — Refine segmentation before verification

Split mixed limitation/factual assertions where the grammar clearly separates them. Retain bounded citation inheritance and table/header context.

A scope heading must not exempt a bullet stating a statutory duty. A sentence such as “The selected evidence did not establish the filing rule, therefore no filing is required” must retain the factual conclusion for ordinary verification.

Validate legitimate scope statements against trusted coverage diagnostics. Keep corpus-absence claims unsupported without corpus-level proof.

#### 3.3 — Refine quantity comparison

Reuse `_durations_equivalent()`; do not add another unit-conversion framework.

Apply equivalence to aligned assertions before generic numeric guards. Compare subject, quantity, unit, and comparator together where identifiable.

Preserve exact day/week conversion. Do not equate calendar months with fixed days, or calendar days with business days. Preserve rejection of 11 versus 12 years and changed deadlines.

Ambiguous cross-clause quantities must not be treated as confirmed contradictions merely because digit sets differ.

#### 3.4 — Refine bounded negation checks

Keep `_aligned_entailment_clause()` and improve only demonstrated failures.

Handle explicit necessary-condition constructions without reversing implication: “A is not valid unless B” can establish that A requires B; it does not establish that B alone guarantees A.

A negation-word mismatch alone must not decide a nuanced paraphrase when clauses/conditions are not aligned. Preserve rejection of direct obligation/prohibition reversals, including cross-language controls.

Do not introduce an entailment model or mandatory semantic-review call.

#### 3.5 — Refine authority handling only where tests fail

Use explicit provision scopes, effective dates, and relationship identities. Do not infer precedence from newer publication or treat document-level `MODIFIES` as whole-document replacement.

Retain pre-admission redaction and post-budget authority checks. Dropping a required modifier must not make a base provision usable.

Metadata that cannot resolve scope remains an explicit limitation. Corpus-wide metadata remediation remains separate.

### Compatibility, tests, and acceptance

Update:

- `tests/unit/modules/conversations/test_grounding_service.py`
- `tests/unit/modules/conversations/test_grounding_modes.py`
- `tests/unit/modules/conversations/test_candidate_wise_grounding.py`
- `tests/unit/modules/conversations/test_current_authority.py`

Keep existing claim statuses, citation identities, and thresholds. Add diagnostic reasons only when necessary and backward-compatible.

Acceptance:

- Known faithful paraphrases stop receiving false contradiction results.
- Negative controls remain unsupported/unverified as appropriate.
- Scope limitations do not hide factual claims.
- Strict/balanced admission results remain unchanged.
- Zero new provider calls.
- Final claim verification remains enabled.
- Measured verification latency does not materially regress on the fixed fixtures.

---

## Execution gates, benchmarks, and handoff requirements

### Delivery order

1. Stage 1A instrumentation.
2. Stage 1B provenance, exact recall, and routing.
3. Stage 2 policy characterization/separation.
4. Stage 2 proof retention, delta review, and selector retries.
5. Stage 3 targeted semantic fixes.

Each change must include tests and a short before/after trace before the next is promoted.

### Benchmark protocol

Use fixed non-sensitive prompts, effective configuration, corpus/index identity, and provider settings. Run at least three repetitions per relevant case; include fresh conversations and the exact three-turn rewrite chain.

Record:

- Commit, configuration snapshot, and corpus identity.
- Conversation/message IDs and exact prompts.
- Server elapsed, client first token, done, message refresh, and final completion.
- Calls and tokens by purpose.
- Recovery attempts, changed/proven/unresolved facets, and stop reason.
- Citation/source locations and factual-support outcomes.
- Response language, shape, useful content, and limitation text.

Report individual runs and median/range. Three repetitions do not establish reliable p95 latency. Do not declare a provider or configuration “best.”

### Required handoff from each implementation patch

- Files/functions changed and why.
- Tests added and commands/results.
- One successful path and one fallback/failure trace.
- Compatibility impact.
- Measured calls removed or retained.
- Known limitations and rollback commit.

### Deferred work

Keep provider/reranker selection, server resizing, cross-turn embedding LRU, entailment models, durable facet graphs, new web-discovery policy, detailed requirement-event infrastructure, corpus-wide metadata remediation, `ape_test`, and fixture cleanup outside these changes.

Investigate infrastructure tuning only after timing spans establish remaining capacity or connection-acquisition bottlenecks. Do not remove useful evidence checks merely because they consume time.
