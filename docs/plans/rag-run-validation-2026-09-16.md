# Post-timeout investigation — 16 September 2026

Companion to `rag-run-investigation-2026-09-16.md`, sections 3–7 and 9. This is an incremental validation record, not a completed release checklist. Baseline: `8dc4698` (deployed timeout fix). Changes described below are local and require deployment before production acceptance.

## Observed production failures

Conversation `50005b50-5052-47bf-a111-8d8c0983b1a2`, project configuration revision 10, source generation 101, translation off:

- Broad inactive-company duties question: 71,471 ms, refused after `coverage_gap_classification_mismatch`. The 300-second timeout was loaded; this was schema rejection, not timeout. Coverage review and retry used 56,064 and 62,405 input tokens. Three LLM calls, nine reranks, 221 embedded texts.
- Focused annual-return duty/deadline: 72,297 ms. Adjacent retrieval recovered both section-36 passages, but the reviewer added an unsolicited requirement for separate RJSC procedural guidance. Answer was partial; its source-limitation sentence was incorrectly treated as a factual claim. Eight LLM calls, eight reranks, 209 embedded texts. Progress stop reason was null.
- Request to restate that answer in three Bangla bullets: refused because online filing procedures/forms/fees remained missing, although the user requested a rewrite of the established duty and deadline.

These are individual observed runs, not repeated-run latency measurements or a matched local/production benchmark.

## Local changes

- Conservatively default misaligned diagnostic gap labels to source-rule gaps; preserve every missing item and all proof validation. Personal-input classification still requires the separate input review.
- Ask coverage reviewers to omit redundant gap labels. Deduplicate identical authority records without discarding distinct outcomes or scopes.
- Prioritize distinct search concepts within the eight-query budget instead of mandating an English/Bangla pair for every concept.
- Do not require a second agency publication merely to repeat a sufficient governing rule. Actual procedure, delegation, conflicts and applicability conditions still require evidence.
- Recognize supplied-source limitation wording through the existing validated coverage path. Remove the instruction to start every partial answer with a mechanical label.
- Emit stop reasons for validated scope, full coverage and exhausted review paths.
- Count actual answer markers in cited metrics and authority summaries. Preserve supplied snapshots and their numbering; expose all of them in the inspector, distinguishing supplied from cited. Reset streaming progress for each request.
- Recognize ordinary English/Bangla rewrite requests without requiring the magic phrases “previous answer” and “no new facts.” Requests that add new/current subject matter retain broad retrieval. Recalled IDs still pass current retrieval filters and source checks.

## Local observations and limits

Local project configuration revision 3 has translation on, unlike production. In conversation `45903399-84a9-4412-94db-56d1b9e84ff6`, the initial answer completed in 22,793 ms, supplied ten passages and cited two. One citation pointed to an unrelated passage while the correct section-36 opening was supplied as passage three. The verifier correctly left that claim unverified. This is not a fully successful answer.

Its ordinary Bangla rewrite missed citation recall and retrieved unrelated sections, producing an answer about absent evidence instead of preserving the earlier topic. The rewrite eligibility fix follows this observation. It does not implement semantic entailment or automatically repair incorrect citation markers.

After that fix, a fresh local conversation (prefix `98f0f97e`) answered the focused question with the section-36 opening and continuation, using markers [3] and [2]; all four claims passed. The same ordinary Bangla rewrite request returned three bullets on the correct topic with those sources, all three claims supported, in 26,614 ms. Coverage recovery was `not_needed`. This is one successful local pair, not evidence that the production scope-expansion failure is resolved across repeated runs.

## Validation and remaining acceptance

- Backend unit, architecture and evaluation run: 1,294 passed; two optional PaddleOCR tests skipped. Subsequent recovery suite: 117 passed; rewrite suite: 11 passed.
- MyPy: 443 source files passed. Ruff lint passed. Changed conversation files pass formatting; repository-wide formatting still flags unchanged `knowledge/services/chunking/models.py` and `platform/config/profiles.py`.
- Frontend typecheck and production build passed. Changed files pass Prettier and `eslint src` passes. Full `eslint .` includes an existing `node_modules.pre-validation` cache and fails on that generated directory.
- Initial concurrent frontend suite had five failures, including relationship UI timeouts. The relationship suite passed in isolation; the full serial run then passed all 94 tests in 20 files (115 seconds).
- Migration graph check passed, head `20260908_0033`. Disposable `ape_test` integration and the `created_by` fixture issue were not revalidated in this pass; no production/local application database was migrated.

Still deferred: semantic entailment, embedding LRU, per-requirement event log, documentary authority metadata audit, production-shaped tax regressions, repeated provider-backed broad/focused/rewrite runs after deployment. No MODIFIES metadata was invented or edited. Headerless-table, long-section and numeric-duration regressions in the deterministic suites do not substitute for corpus-level acceptance. The plan's section-9 checklist remains open.
