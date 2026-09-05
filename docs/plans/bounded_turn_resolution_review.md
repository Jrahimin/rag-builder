# Bounded turn resolution implementation review — 2026-09-05

Reviewed the uncommitted implementation against `bounded_turn_resolution_plan.md`.
Refinements remain in the working tree; no commit was created.

## Findings fixed

- **Referenced values were not validated.** Existing message IDs and matching roles
  could authorize an invented active value. Validation now checks literal text and
  actual citation identity/date fields. Adoption requires the referenced assistant
  value and a later user instruction. Numeric normalization preserves signs,
  decimals, currencies, and units instead of comparing only concatenated digits.
- **Snapshot origin could grant unearned authority.** Exact-date and day-before
  operations now require a validated user/adopted date binding matching the anchor.
  A model-supplied origin cannot authorize a citation-only or unrelated date.
  Standalone and fallback paths discard model-derived snapshots and interpretations.
  Conflicting request dates clarify without retrieval; derived snapshots suppress web.
- **Interpretation could blur assumptions and references.** Generation now labels
  conversation bindings explicitly: assistant references identify what to verify,
  while user literals/adoptions supply scenario assumptions. Resolver prompt v2
  explains referential adoption, verification, short clarification replies, topic
  reset, hypothetical rules, presentation requests, and numeric-only language
  continuity. Citation metadata is supplied once rather than duplicated in the prompt.
- **Usage totals could hide an unknown resolver cost.** Bypasses and attempted calls
  with unknown usage are distinguished. Incomplete totals remain null. Journey
  reports complete-usage coverage and per-conversation resolver usage.
- **Journey did not read production snapshot diagnostics.** It now reads the actual
  `snapshot` record, rejects stale additional active operands, checks clarification
  invariants, and includes execution failures/blocked expected turns in resolution
  correctness rather than silently crediting or excluding them. `filter_changed`
  records a derived snapshot separately from query changes.
- **Replay comparability was incomplete.** Raw retrieval comparisons now use the
  production configuration hash, check source metadata generations, and exclude
  translation degradation as well as reranking degradation.
- **Partial repository boundaries silently weakened history isolation.** Both
  `created_at` and `id` are required whenever a boundary is supplied.

## Focused validation

- **268 passed; 6 skipped** across resolver/contracts/safety, ChatService normal/SSE,
  prompt/history repository, Journey, grounding, Evidence Quality production parity,
  architecture boundaries, and targeted integration tests.
- All six integration skips were due to unavailable PostgreSQL: three Journey
  smoke tests and three Evidence Quality API tests.
- Resolver-only validation after the timeout-bound refinement: **6 passed**.
- Focused Ruff checks passed; Mypy passed for the four reviewed runtime modules;
  `git diff --check` passed.
- Compared fixture JSON with HEAD: all **21 original tax cases and assertions are
  unchanged**. Development sequences and held-out examples were not weakened or tuned.

## Deferred evidence and scope

This is not a release accuracy or performance result. Full provider-backed Journey
sequences, measured improvement over the pre-resolver baseline, latency/token/cost
measurements, and the independently authored held-out 90% check remain outstanding.
The checked-in phase-one baseline describes expected failures; it does not substitute
for paired provider-backed measurements.

Deterministic validation establishes literal provenance and scope, not semantic
correctness. Selecting the intended referent, recognizing adoption versus verification,
and choosing the corrected operand still require model evaluation. Scripted unit
responses prove orchestration and validation, not real-provider interpretation accuracy.

The uncommitted Project AI settings UI/provenance changes are outside this approved
plan and were left untouched. Review them separately before including them in the
same commit. Retrieval algorithms, evidence/claim gates, provider architecture,
Project configuration schemas, and database schema were not changed by this review.
