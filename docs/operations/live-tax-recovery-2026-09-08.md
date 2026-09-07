# Income-Tax evidence recovery follow-up

Project: `f932c9e0-40af-4ff3-8704-ac3e542f2a8c`.

## Changes

- Recovery **v13** retains initial admitted evidence and permits one focused follow-up, at most two searches, based on the completeness review's missing requirements. The combined evidence is budgeted, reconciled for amendment effects and checked again. The original snapshot, filters, date, relevance thresholds and exact-quote checks remain enforced. The existing 120-second overall timeout bounds the additional work.
- Planning and review distinguish already-taxable inputs from gross salary. An evidenced national rule can establish applicability to a city; a separate city-specific provision is not presumed necessary.
- Provider-neutral JSON validation tolerates a single enclosing JSON fence and permits one format-only retry for malformed output. Truncated responses still fail closed. No partial JSON or invented quotations are salvaged.
- Relationship candidate budgeting reserves a candidate for each retrieved modifier before filling remaining slots by rank. Source discovery does not bypass downstream relevance/authority admission.
- Chunker **3.3.0** carries available parser heading text into continuation paragraphs when it fits the token budget, as well as retaining existing table context. Chunk validation does not merge neighboring passages with different known heading scopes. This prevents a minimum-size heuristic from joining current and future sections.

## Deployment

1. Deploy API and workers together. Keep the OCR ceiling at **500**; no further OCR configuration change is required.
2. Verify new processing snapshots use chunker **3.3.0**. Its default is updated in the configuration model, processing metadata and index profiles. Remove an explicit older chunker-version override if present.
3. Reprocess the tax rule documents whose stored chunks are 3.2.0, especially Finance Act, both base Acts, Nirdeshika and Paripatra. Refresh the remaining legal instruments too for consistent heading handling. Vector-only rebuilds cannot change stored text. Process serially with automatic builds, waiting for READY and successful index activation before starting the next document.
4. Preserve source generation 35's corrected dates and multiple Modifies targets. Do not invent Ordinance 59 commencement or change SRO 273's relationship from its filename.
5. Verify the final active manifest contains all ten documents with matching chunk, vector and keyword counts. Retain the previous build and its provider credentials for rollback.

## Acceptance after deployment

Use new conversations so their runtime snapshots reflect the deployment. Test the original gross-salary question and the explicit already-taxable variant from `artifacts/tax-investigation/live-validation-2026-09-08.md`.

Require a generated calculation, explicit assessment year, scenario assumptions, correct input treatment, governing per-rule citations and consistent arithmetic. A valid refusal is not a pass for these answerable cases. Check v13 diagnostics: `initial_coverage`, `focused_queries`, final `coverage` and retained chunk IDs expose any focused retry. Searches must discover the applicable rules; no expected rates or answer totals are injected into production prompts.

Also test an explicit historical year, adjacent future-year sections, unsupported/conditional commencement, malformed completeness output and a changed index snapshot. These must not be silently treated as current supported evidence.

## Validation and limits

637 targeted unit tests passed across conversations, retrieval, chunking, configuration and `rag_journey.py`. New regressions cover focused recovery, snapshot changes, a bounded unsuccessful follow-up, JSON-format recovery, modifier candidate fairness and heading continuity through chunk validation.

These are local deterministic tests, not a new live model result. Live acceptance requires deployment and reprocessing. The full CLI journey still needs its database/runtime; it does not attach directly to this existing live project. Missing parser headings or context exceeding the token budget remain explicit limitations, not a reason to assume applicability from a filename.
