# Refined plan: bounded turn resolution, proven through RAG Journey

## Implementation status (2026-09-05)

Phases 1–3 are implemented on this branch. Production chat runs one bounded
turn-resolution step before retrieval. Development packs are `tax_v1` and
`business_conversation_v1`. Journey reports resolver latency, usage, fallback,
standalone/bypass, input-change, optional raw-retrieval replay, and continuity
aggregates. Held-out 90% accuracy remains a later release check after freeze;
the protocol is documented in `docs/features/turn_resolution_held_out.md` and
no held-out examples are in this repository. Provider-backed full Journey is
an operator command, not an echo-LLM CI gate.

---

## 1. Direction and boundaries

Keep the existing design: **one bounded turn-resolution step inside Conversations, before retrieval**.

The goal is natural continuation of a source-based conversation: users can refer back, adopt a previous result as a scenario input, correct a parameter, change topics, compare sources, or answer a clarification without repeating the whole question.

Maintain these boundaries:

- Conversation determines the current question and scenario.
- Current request filters determine explicit scope.
- Fresh retrieval and admitted evidence establish factual authority.
- Generation receives the original message, validated interpretation, bounded history, and current evidence as separate inputs.
- Claims and citations refer only to current evidence.

Keep retrieval algorithms, authority selection, grounding thresholds, `EvidenceUnit`s, provider architecture, and Project configuration unchanged. No persistent scenario state, summaries, agents, planners, or additional production retrieval passes.

RAG Journey remains the primary end-to-end continuity test. Evidence Quality remains a separate retrieval/grounding evaluation system.

## 2. Concrete component and lifecycle

### Component and contracts

Add:

- `E:/python-projects/rag-builder/backend/app/modules/conversations/turn_resolution.py`
- `E:/python-projects/rag-builder/backend/app/modules/conversations/prompts/turn_resolution.py`

Use one concrete `TurnResolver` through the existing conversation `BaseLLMProvider`. Keep immutable contracts, validation, and deterministic temporal conversion in the first file; keep one versioned resolution prompt in the second.

| Contract | Contents |
|---|---|
| `TurnResolutionInput` | Original current message and ID; bounded preceding messages; bounded citation identity/date metadata; current request filters; captured UTC reference date. |
| `ReferenceBinding` | Kind, active value, origin, and supporting references to supplied messages/text or citation fields. |
| `TurnResolution` | Outcome, relation, effective question, active bindings, temporal intent, and optional clarification question/reason. |
| Effective retrieval inputs | Constructed by application code: effective query, unchanged document/metadata filters, and validated effective `as_of`. |

Outcomes: `standalone`, `resolved`, `clarify`, `fallback`.

Relations: standalone, follow-up, correction, topic change.

Binding kinds: topic/entity, scenario parameter, period/date, source.

Binding origins:

- `user_literal`: value supplied directly by a user.
- `user_adopted_assistant`: prior assistant-produced value explicitly adopted by a subsequent user instruction.
- `assistant_reference`: referent identification only.
- `citation_reference`: source identity or date reference only.

For adoption, retain **both** the assistant value reference and the user adoption instruction reference. Derived referenced-message IDs come from these bindings.

The model cannot output document filters, arbitrary `as_of`, evidence, answers, citations, confidence scores, or reasoning prose. Do not consume previous resolution records as conversational memory.

### History

Extend [MessageRepository](/E:/python-projects/rag-builder/backend/app/modules/conversations/repositories/message_repository.py:45) with an exclusive `(created_at, id)` boundary.

Supply at most `min(max_history_messages, 8)` preceding messages and 16,000 characters including citation metadata. Drop oldest complete messages to fit. Never include later messages, fetch additional history to repair references, or truncate a message into a misleading fragment.

Exclude system messages and failed assistant responses. Include clarification questions. Supply citation identities, titles, and date fields rather than historical evidence excerpts or claim records.

### Production lifecycle

Integrate through [ChatService._prepare_turn](/E:/python-projects/rag-builder/backend/app/modules/conversations/services/chat_service.py:448), shared by normal and streaming responses:

1. Persist the original user message through the existing first transaction.
2. Load preceding history and capture immutable message, conversation, provider, and prompt inputs.
3. Resolve the conversation LLM and temperature before releasing the read transaction.
4. Run resolution and deterministic validation.
5. Return clarification directly when appropriate.
6. Otherwise retrieve once using effective inputs; use the same effective question for admission and passage rescue.
7. Apply effective scope consistently to modifier notices and web suppression.
8. Generate from current evidence and persist through existing transaction boundaries.

Do not mutate the request or original message. Release database read transactions before resolver and generation I/O; retain existing ORM-refresh safeguards.

### Invocation and fallback

- Existing casual turns and turns without usable history bypass the model.
- Other turns with history may make one resolution call. Do not restore language-specific substring heuristics.
- Use `temperature=None`, at most `min(llm.max_tokens, 2048)` output tokens, and a 10-second outer timeout bounded by the provider timeout.
- Validate strict JSON with Pydantic; reject extra fields. No repair loop or resolver retries.
- Timeout, malformed output, invalid references, and provider failure discard the entire interpretation and preserve the existing raw-message path and original filters.
- Cancellation propagates instead of becoming fallback.

These bounds are initial implementation defaults, not claimed latency targets. Measure their effect before considering optimizations.

### Generation and clarification

Extend [PromptBuilder](/E:/python-projects/rag-builder/backend/app/modules/conversations/prompt_builder.py:24) with optional validated interpretation outside evidence blocks. Keep the original message unchanged as the final user message.

Resolved turns retain bounded history. Recognized standalone questions and topic changes omit preceding topic history. Fallback preserves existing generation-history behavior.

Update the single canonical prompt and its version to distinguish scenario assumptions, conversation references, and current evidence.

**Represent clarification as grounding not applicable:**

```text
finish_reason = clarification
source_provenance = none
claims = []
citations = []
grounded = null
insufficient_evidence_reason = null
evidence_gate.claims_status = not_applicable
evidence_gate.generation_ran = false
evidence_funnel.outcome = clarification
```

The ORM and response schema already support nullable `grounded`. However, `ChatService` currently converts null to false when generation did not run. Add an explicit clarification branch that bypasses this conversion and claim mapping.

Do not change existing greeting, refusal, or polarity-only semantics. Distinguish `not_applicable` from the existing `no_verifiable_claims` result.

Expose `finish_reason` and a compact resolution summary in SSE `done`. No database migration or new request field is required.

## 3. Invariants and natural conversational behavior

### Assistant-derived values: permit adoption, not factual promotion

A prior assistant result may become a scenario input when the user clearly adopts it:

```text
Assistant: The calculated reimbursement is 7,500.
User: Use that amount as my next monthly budget.
```

Resolve the budget to 7,500 with `user_adopted_assistant` provenance. Retrieve fresh evidence for the next question. Do not require the user to retype the number or reconfirm an unambiguous instruction.

Allow referential adoption such as “calculate the fee on that amount,” not only the literal phrase “use that.”

Apply these limits:

- Mere mention in an assistant answer is not adoption.
- An ambiguous reference among multiple amounts requires clarification.
- “Was that amount correct?” requests verification; it does not adopt the amount.
- An adopted value is a scenario assumption, not proof that the previous answer was correct.
- A user-adopted rate or rule cannot become an established policy fact. Verify applicability against fresh sources; keep hypothetical assumptions distinguishable from documented rules.
- Existing calculation and claim verification remain unchanged. Adoption does not grant unsupported claims a grounded status.

This same rule permits a user to explicitly adopt a previously mentioned date for a snapshot request. Source metadata alone still cannot change filtering.

### Parameters and corrections

Preserve values, units, currencies, signs, and their source references. Normalize Unicode digits and separators for validation, while preserving display text.

Latest explicit user corrections replace prior active bindings. Do not require every number in a correction to survive: “90,000, not 75,000” intentionally removes the earlier amount.

Validate literal references and allowed origins deterministically. Identifying the intended correction or adoption remains semantic; test it independently rather than treating schema validity as proof.

Do not blanket-prohibit mentioning an old value in a correction explanation. Assert the active operand and resulting answer, while using prohibited tokens only where they genuinely establish an incorrect result.

### Time and `as_of`

Capture one UTC reference date per turn for interpretation. The model returns temporal intent and an anchor; code performs calendar operations.

Rules:

1. Current request `document_id`, metadata filters, and `as_of` remain authoritative and per-request. Omitted previous API filters never become sticky.
2. Semantic periods, publication years, and mentioned dates may affect the question without changing authority selection.
3. For resolved snapshot requests, accept an unambiguous ISO calendar date supplied by the user or explicitly adopted from a uniquely identified prior value. Validate the referenced date and adoption before constructing UTC midnight.
4. Support only bounded deterministic relative operations: today, yesterday, and the calendar day immediately before an identified exact date.
5. Year-only periods and expressions such as “before that” do not authorize arbitrary year-end, previous-year, or amendment cutoffs. Clarify only when an exact snapshot is necessary.
6. A conflicting supplied request `as_of` produces clarification; interpretation never replaces it.
7. Citation dates without user adoption cannot independently authorize a cutoff.
8. Any derived historical snapshot suppresses web access as an explicit snapshot would.

Do not introduce general date parsing, fiscal-calendar inference, or multi-snapshot retrieval. Standalone requests retain their existing behavior.

### Behavioral expectations

| Situation | Behavior |
|---|---|
| Follow-up or simpler explanation | Resolve the subject and requested presentation; retrieve fresh supporting evidence. |
| New or corrected scenario amount | Carry the question, replace the active input, and obtain the governing rule again. |
| Adopt a previous calculated value | Accept an unambiguous user instruction with dual provenance; treat the value as scenario input. |
| Different category or instrument | Retrieve applicability rather than inherit the previous conclusion. |
| Ambiguity → “premium” | Retain the clarification exchange and resolve the short answer to the intended plan. |
| Topic change | Drop old topic-specific bindings and conversational dates; preserve current request filters. |
| Language switch | Resolve across English, Bangla, and code-switched history; follow the latest language instruction. Numeric-only replies retain the established language. |
| Source comparison | Bind both identities, retrieve fresh evidence, and identify a missing side. Old citation numbers are never reused. |
| Hard scope | Preserve filters, authority notices, and web suppression; do not broaden scope to satisfy a follow-up. |
| Missing recent referent | Clarify when recognized; do not manufacture remembered context. |

### Diagnostics and accounting

Persist compact `metadata.turn_resolution`:

- Version, outcome, relation, reason.
- Effective question, active bindings, and provenance.
- History size/truncation and referenced message IDs.
- Temporal intent, effective snapshot, and snapshot origin.
- Provider/model, latency, finish reason, usage, and sanitized failure code.
- Separate booleans for query changes and filter changes.

Do not store raw malformed responses or reasoning.

Keep resolution and answer-generation timing separate. Include known resolver usage in turn totals exactly once; unknown usage remains unknown. Preserve completed resolution diagnostics if later generation fails.

## 4. RAG Journey, reproducible measurement, and held-out validation

### Ordered representation and execution

Extend [JourneyManifest and JourneyCase](/E:/python-projects/rag-builder/backend/app/cli/rag_journey.py:103) additively:

```text
JourneyManifest:
  existing sources, anchors, cases
  sequences = []
  reference_time = optional UTC timestamp

JourneySequence:
  key, tags, turns: list[JourneyCase]

JourneyCase additions:
  metadata_filter = {}
  expected_resolution = optional structured expectation
  mode additionally accepts clarification
```

Schema-v2 fixtures use sequences; version 1 remains supported. Resolution expectations assert outcomes, active values/origins, prior turn references, temporal intent, and effective snapshots—not exact rewritten prose.

In [_run_variant](/E:/python-projects/rag-builder/backend/app/cli/rag_journey.py:1571):

- Preserve each existing case as a fresh conversation.
- Create one conversation per sequence.
- Execute turns in order with fresh sessions and freshly composed `ChatService` instances.
- Let production persistence provide actual history.
- Never insert expected answers or inherit request filters.
- Isolate conversations across variants.
- Continue after assertion failures, recording upstream failed turns. Block remaining turns after an execution failure that prevents continuation.

Retain flattened results and existing case keys; add sequence/turn/message identities and whole-sequence outcomes.

### Deterministic time

Require `reference_time` for new sequence packs. Add a narrowly scoped Journey clock context around turn execution that fixes only:

- Conversational reference-date reads.
- Default source-policy date reads in `SearchService`.
- Default date reads in `knowledge/source_metadata_read.py`.

Use harness-local scoped patching; no public clock configuration or production clock framework. Apply the same fixed clock to diagnostic retrieval replays and restore it afterward.

Do not freeze database timestamps, ingestion/index activation, timeout clocks, polling, or `perf_counter`. Do not inject `as_of` into requests that omit it.

Keep existing case definitions and assertions unchanged. Give the tax pack a documented fixed reference time compatible with its existing dated expectations. Record both simulated reference time and real execution timestamps in artifacts.

Unit tests use fixed reference dates. Verify identical interpretation and authority selection when the host date changes, plus UTC-midnight, month-boundary, and leap-day subtraction cases.

### Development fixtures

Preserve all 21 tax cases. Add authority/temporal sequences using the same corpus.

Add:

`E:/python-projects/rag-builder/tests/fixtures/journeys/business_conversation_v1/journey.json`

Use the previously proposed expense-policy/amendment and standard/premium-support documents. Keep corpus terminology and expected values out of production code.

Required sequence intentions:

- Continue a calculation, adopt its result into a new scenario, then replace it.
- Change period, resolve temporal ambiguity, and continue at an exact snapshot.
- Clarify a reference and successfully answer the short disambiguating reply.
- Reset topics without carrying the old amount or category.
- Switch languages and correct Unicode-digit parameters.
- Compare sources, narrow hard scope, and verify omitted transport filters remain non-sticky.

Test ambiguous adoption and factual re-verification in deterministic resolver fixtures. Avoid redundant sequences added merely to increase counts.

### Failure attribution and cost measurements

Add `turn_resolution` before existing Journey failure stages. Keep all downstream observations and identify the earliest failed stage.

Clarification:

- Has no retrieval, web search, or factual answer generation.
- Is excluded from answerable recall, refusal, citation-coverage, and groundedness denominators.
- Counts toward clarification precision/recall and whole-sequence success.
- Fails an answerable turn when unexpected.

Report:

| Measurement | Definition |
|---|---|
| Resolver latency | p50/p95 over attempted calls, including timed-out attempts; also show total-turn latency. |
| Usage/cost | Resolver tokens per attempted call and per conversation; share of total tokens; cost estimate only with a recorded rate card or reported charges. Unknown cost is null. |
| Fallback rate | Fallbacks divided by attempted resolutions, grouped by reason. |
| Standalone rate | Standalone/topic-change outcomes among attempted resolutions; bypasses reported separately. |
| Input-change rate | Changes to query and filters, reported separately. |
| Retrieval-change rate | Paired differences in retrieved set/rank and required-anchor recall, not merely changed query text. |
| Continuity | Per-turn resolution correctness and complete-sequence success. |

For retrieval-change measurement, add an **optional Journey-only diagnostic replay** of raw query plus original request filters against the same corpus/configuration snapshot. Run after the production turn; never generate from or feed replay results into history.

Compare replay results with production retrieval. Record snapshot mismatches and provider degradation as non-comparable pairs. Report replay latency/cost separately from user-facing turn measurements. Production still performs one retrieval.

Remove resolution time from the Journey’s residual “grounding and context” timing.

### Genuinely held-out validation

Development Journey examples are not the held-out set.

Before final evaluation:

1. Freeze the resolver prompt/version, validation rules, model/settings, and development fixtures.
2. Have an independent evaluator or reviewer author unseen scenarios under a predeclared coverage rubric. Keep exact examples outside implementation/tuning sessions until the candidate is frozen.
3. Include novel entities, amounts, wording, domain context, adoption patterns, corrections, ambiguity, standalone turns, and temporal references—not cosmetic rewrites of development cases.
4. Lock the dataset hash, scoring rules, and denominator before execution.
5. Score every applicable turn, including fallbacks/timeouts; report results by category and total sample size.

Target at least 90% structured resolution accuracy, with clarification accuracy reported separately. This is a release check on that sample, not a universal reliability claim.

If failures lead to tuning, the exposed set becomes development data. Evaluate the revised candidate on a fresh held-out set; retain the earlier results.

### Evidence Quality

Keep its API, dataset schema, worker, and profile architecture unchanged. Retain existing shared metrics and single-turn parity tests.

Document that its existing `previous_user_query` field does not demonstrate continuity. Do not merge Journey sequences into Evidence Quality in this change.

## 5. Three cohesive implementation increments

### Phase 1 — Contracts, deterministic policies, and Journey baseline

**Deliverable:** independently testable contracts and a sequence-capable harness; production chat remains unchanged.

Implement:

- Resolution input/output models and pure binding/date validation helpers.
- Sequence manifest validation, execution, clarification expectations, and fixed-clock support.
- Initial tax continuity sequences and baseline artifacts.
- Development/held-out separation and scoring protocol.

Affected areas: new resolution module, Journey runner, [Journey unit tests](/E:/python-projects/rag-builder/tests/unit/cli/test_rag_journey.py), fixture manifests, and [Journey documentation](/E:/python-projects/rag-builder/docs/features/test_rag_journey.md).

Acceptance:

- Existing 21 cases and assertions remain intact.
- Ordered turns reuse persisted conversations and isolate variants.
- Reference-time tests are reproducible without injecting scope.
- Pure tests cover adoption provenance, parameter replacement, and deterministic date operations.
- Current continuity failures are captured without relaxing expectations.

**Session handoff:** schema/contracts, clock semantics, baseline artifacts, and known failing sequence steps.

### Phase 2 — One working production vertical slice

**Deliverable:** bounded resolution integrated through normal and streaming chat, proven by representative real-history sequences.

Implement:

- Versioned resolver prompt and one-call execution.
- History boundary and ORM-safe lifecycle.
- Effective retrieval inputs and optional prompt interpretation.
- Clarification with `grounded=null`.
- Basic per-turn diagnostics, timing, and usage accounting.
- Representative follow-up, adopted-value, correction, and clarification-continuation tests through production `ChatService`.

Acceptance:

- Original content and explicit filters remain authoritative.
- Fresh evidence supports factual answers; adopted values remain scenario inputs.
- Timeout/invalid-output fallback and cancellation behave correctly.
- Clarification persists and streams consistently without evidence claims.
- No read transaction remains open during resolution.
- Existing single-turn, grounding, and Evidence Quality parity tests pass.

**Session handoff:** working integration, resolver version, populated diagnostic examples, and remaining cross-domain validation work.

### Phase 3 — Cross-domain proof and release measurements

**Deliverable:** complete business-domain coverage, observable cost/benefit, and untouched held-out evaluation.

Implement:

- Business fixture pack and remaining language, topic-reset, comparison, scope, and temporal sequences.
- Resolution/clarification aggregates, latency distributions, usage/cost reporting, and optional raw-retrieval replay.
- Full Journey integration smoke coverage and provider-backed validation.
- API/runtime documentation and focused ADR-020 update.

Finish development validation before freezing the candidate and revealing held-out examples.

Acceptance:

- Intended development sequences pass, including adoption followed by correction and clarification followed by a short answer.
- Held-out resolution accuracy reaches 90%, with sample size, category results, and all fallbacks disclosed.
- Complete-sequence success improves over baseline without weakening existing grounding or citation gates.
- Zero scope violations or incorrect accepted bindings in the safety suite.
- p50/p95 latency, token/cost impact, fallback rate, standalone rate, and measured retrieval changes are reported.
- No new Project settings, database migration, or unrelated architecture changes.

**Build bounded turn resolution next, allowing explicit adoption of prior results while requiring fresh sources for factual answers.** This improves enterprise, finance, legal/compliance, research, document-assistant, and embedded-SaaS conversations without converting history into authority. The principal risk remains a well-cited answer to the wrong interpretation; structured resolution checks, complete Journey sequences, and genuinely held-out validation provide the release evidence.
