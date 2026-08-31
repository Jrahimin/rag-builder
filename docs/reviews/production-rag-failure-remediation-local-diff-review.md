# Production RAG Failure Remediation — Local Diff Review

Uncommitted work on `main` (not pushed). Reviewed against the two-phase plan: candidate-wise multilingual grounding, then `MODIFIES` recall and OpenAI web-fallback contract repair.

**Verdict:** The chat/retrieval implementation matches the *mechanics* of the plan (typed variants, span-stable units, shadow flag default off, depth-one expansion, source-owned web text). It does **not** meet the plan’s primary Phase 1 acceptance criterion: the captured production turn still cannot be admitted without changing thresholds. Several lifecycle surfaces (evaluation, search API, expansion observe-mode) were not updated to the new contracts.

---

## 1. Blocking contradiction: captured turn vs Phase 1 acceptance

Plan: *“The reproduced case admits valid Bangla evidence without changing thresholds.”*

The fixture `tests/fixtures/evaluation/phase1_multilingual_grounding_production_shape_v1.json` records the opposite, and the code agrees:

| Captured fact | Implication |
| --- | --- |
| `translated_lexical:bn = 0` | No translated-lexical branch contribution exists to use as an independent signal. |
| Original semantics `0.21–0.27` | Below `0.35` and below the `0.30` cross-language floor. |
| Translated dense `~0.27` on rank 1 | Shadow-only by design; cannot admit. |
| Reranker `0.71–0.89` | Clears `0.40`, but corroboration still fails. |
| `post_implementation_read_only_replay.candidate_wise_sufficient = false` | Hand-written, and consistent with the gate. |

Even if translated-query token overlap were allowed without a lexical-family contribution, rank 1’s bounded excerpt vs the translated query shares only `উৎস` and `কর` (**coverage 0.33 < 0.50**). Morphological mismatch (`উৎস`/`উৎসে`, `কর্তনের`/`কর্তন`, `ক্ষেত্রগুলো`/`খাত`) is exactly the generic-overlap case the hard-negative test rejects.

The English→Bangla unit test admits a **synthetic paraphrase** with an injected `translated_lexical` contribution the production capture did not have. The fixture is only shape-checked; it is never replayed through `GroundingService`.

Finance Act 2026 remains `classification_unresolved`. That is allowed not to block the phase, but the failing document is a 2025 ordinance with `legacy_governance_unspecified`. Current-authority expansion cannot recover this turn until that classification exists **and** candidate-wise would actually admit the hit.

**Net:** Phase 1 as implemented is a correct fail-closed gate. It does not remediate the recorded production refusal. Shipping the canary will not change that turn.

---

## 2. What matches the plan

### Phase 1

- Shared `QueryVariant` / `BranchContribution` in `platform/domain/evidence_contracts.py`; conversations do not import retrieval internals.
- Planner emits original + translated variants; RRF, hydrator, and the conversations adapter preserve them.
- Rank-ordered assessment; lower candidates can pass after rank one fails.
- Span order: scored passage → complete chunk → match-local sentence span; full-chunk cosine is not relabeled onto a match-local span (`semantic_span_aligned=False`).
- Canonical `reranker_relevance:v1`; threshold unchanged (`0.40`).
- Translated dense recorded only as shadow scores.
- Chat path: assess full retrieved chunks, then `ContextBuilder` omits whole `EvidenceUnit`s instead of slicing them.
- Flag `candidate_wise_grounding_enabled` defaults **false**; chat always computes both paths (shadow vs canary).
- Compact per-candidate assessments plus retrieved / admitted / context-selected / cited counts.

### Phase 2A

- Incoming `MODIFIES` only; depth one; visited revision/document sets; caps 8 / 20.
- Same query-variant set on related branches; one combined reranker call.
- `relationship_grounding_trust=False`; relationship provenance cannot admit.
- Defaults off; capture still runs when expansion is on even if source policy is off.
- Post-rerank removal / unfilled-slot diagnostics added without changing consolidation.

### Phase 2B

- Requests `web_search_call.results` and `web_search_call.action.sources`.
- `WebDiscoveredSource` vs `WebEvidence`; assistant `output_text` never becomes evidence.
- Associate by provider ID, else canonical URL; conflicting ID/URL rejected.
- Conservative HTTP(S) canonicalization (scheme/host/IDNA/default ports/fragments).
- Chat emits one terminal status: `no_sources` / `sources_found_no_extractable_evidence` / `evidence_extracted_irrelevant` / `evidence_accepted`.

---

## 3. Findings

Severity: **H** must fix or explicitly drop the acceptance criterion before canary. **M** correctness/rollout hole. **L** contract or test gap.

### H1 — Production acceptance is unmet (and untested)

See §1. Either rewrite the Phase 1 success bar to “fail closed on this capture,” or change corroboration (not in this plan’s non-goals: no threshold drop, no translated-dense admission). Do not treat the synthetic Bangla test as a reproduction of the captured turn.

### H2 — Evaluation still uses the old evidence lifecycle

`GroundedEvaluationAnswerAdapter` (`composition/evaluation.py`):

1. Truncates via `ContextBuilder.select` **before** `assess()`.
2. Calls `assess()`, then prompts and `map_claims` on those truncated chunks — **ignores `admitted_units`**.
3. `QualityHit` drops `query_variants`, `branch_contributions`, and `evidence_calibration_id`.

When the canary flag is on, chat and evaluation will disagree. Truncation-before-assess is the exact bug Phase 1 was meant to close. Enabling the flag also cannot produce translated-lexical admissions in eval.

### M1 — Translated lexical is gated on branch family, not on the translated variant

Admission requires `family.startswith("translated_lexical")`. A candidate retrieved only via `translated_dense` cannot use translated-query token overlap, even though the plan’s wording is “contributing translated `QueryVariant`.” Production had translated-dense hits and zero lexical hits, so this gate and the capture agree on refusal. If the intent was “any contributing translated variant,” the code is stricter than the plan (and still would not clear 0.50 on the captured excerpt).

### M2 — Expansion has no observe-only mode

Plan rollout: disabled **and** emit eligibility/exclusion diagnostics. Implementation: disabled → empty records / `status=disabled`. First enable is a live recall change. There is no `OBSERVE` analogue to source policy.

### M3 — Search API leaks translated query text

Diagnostics redact translated variant text unless `persist_translation_text`. Each `RetrievalResult.query_variants` still carries full `QueryVariant.text`. Hit metadata previously stripped `translated_query`. Public search JSON now re-exposes it.

### M4 — `document_id` search is no longer single-document when expansion is on

Related retrieval clears `document_id` and searches modifier IDs. Integration test expects the modifier document in a base-scoped search. That is a public search-contract change; callers using `document_id` as a hard scope will see extra documents.

### M5 — Stale / replaced modifiers are labeled `cross_project_or_generation`

If the activated revision ≠ edge modifier revision (REPLACES / newer activation, including a later draft), the outcome is `cross_project_or_generation`. Functionally fail-closed; the reason is wrong for operators and there is no explicit `REPLACES` test.

### M6 — Assessments are not “every reranked candidate”

Grounding sees chat/search hits **after** rerank return_n, source-policy consolidation, hydration drops, and duplicate suppression. Policy-removed or window-dropped candidates get no `CandidateEvidenceAssessment`. Removal counts exist on retrieval diagnostics only.

### M7 — Observe + canary can reintroduce truncated non-units

If candidate-wise admits units that all exceed remaining budget, enforce correctly refuses. Observe mode falls back to `legacy_selected`, which may still slice chunk text. Default gate is enforce; the combination is still a footgun.

### L1 — Calibration mismatch fail-closes, missing provenance does not

Mismatch → `calibration_mismatch` (reject). Missing ID → `missing_compatibility` (continue). Plan said “diagnosed.” Asymmetry is defensible fail-closed, but a wrong ID on all hits would refuse the whole turn.

### L2 — Citation / claim excerpts still slice unit text

Unit id, offsets, and span hash are stable. `citation_excerpt_max_chars` (default 200) still truncates the stored excerpt. Identity tests pass; “unit unchanged through citation snapshots” is only true for identity fields.

### L3 — Web `evidence_id` hashes full text, stored content may be truncated

`max_evidence_chars` bounds `WebEvidence.content` while the id hashes the unbounded string.

### L4 — `discovered_source_count or len(evidence)` fallback

If a provider omits `discovered_sources`, chat can conflate sources with evidence again. Current OpenAI adapter and new fakes populate both; the fallback remains a regression trap.

### L5 — Knowledge checks index membership via `ChunkKeywordIndex`

Allowed through `app.models`, but knowledge now depends on a retrieval table to decide `not_in_active_index`. Embedding-only rows would be excluded.

---

## 4. Plan task coverage (condensed)

| Plan item | Status |
| --- | --- |
| P1 capture + sanitize production turn | Partial: identities/scores/excerpts present; Finance Act unresolved; replay not executed in tests |
| P1 typed variants + branch provenance | Done on retrieval → chat |
| P1 span selection + candidate-wise gate | Done on chat path |
| P1 immutable units through prompt/citation/claims | Done on chat path when flag on |
| P1 shadow then canary | Chat yes; evaluation no |
| P1 every reranked candidate assessed | Partial (visible hits only) |
| P1 production admission | **Not met** |
| 2A incoming MODIFIES, depth 1, caps, one rerank | Done |
| 2A observe-only while disabled | **Missing** |
| 2A REPLACES / historical as_of | Historical `outside_as_of` integration exists; REPLACES only via mislabeled activation check |
| 2B source vs evidence, results+sources parse, URL canonicalize | Done |
| 2C combined retrieval+grounding+prompt+citation+web | Partial: search integration and chat web tests are separate; no chat test that expansion prevents web fallback |
| Eval / QualityHit provenance | **Not in diff** |

Listed plan files with **no** matching diff: `test_grounding_service.py` (legacy still valid at default flag), `test_context_builder.py` (covered in new candidate-wise tests), `test_source_policy.py`, `web_search_factory.py` (unchanged; factory test still passes).

---

## 5. Test gaps vs plan list

Covered well: synthetic EN↔BN lexical admission, rank-2 after rank-1 fail, mixed/numeral/punctuation/generic overlap, translated-dense non-admission, passage offsets, match-local vs no-span, unit identity on chat builders, expansion governance outcomes, one reranker call, incoming-only integration, web dict/SDK/URL/cross-source/assistant-text, web terminal statuses in chat.

Missing or weak:

- Production fixture **executed** through `assess_candidate_wise` (today it would assert `sufficient is False`).
- Explicit `latin_ambiguous` routing (profile is on the fixture only).
- `REPLACES` expansion exclusion by name.
- Depth-two graph (only cycle via overlapping IDs).
- Related branches asserted to reuse the same `QueryVariant` objects (integration infers via search hits).
- Relationship-only candidate through hybrid **then** grounding (unit test is grounding-only).
- Chat: current-authority hit prevents `INDEXED_THEN_WEB`.
- Eval adapter: EvidenceUnit selection / variant provenance.

---

## 6. Edge cases the code handles

- No-reranker: candidate-wise falls back to legacy; canary uses truncated legacy selection.
- Missing query variants: synthesized original from the question; flagged, not rejected.
- Related RRF cap: modifiers with hits but zero retained IDs → `candidate_cap_exceeded`.
- Web URL-only and empty collections: fail-closed, not `no_sources` when sources exist.
- `INDEXED_AND_WEB`: source_kind keeps knowledge vs web citation identity distinct.

---

## 7. Before canary

1. Drop or restate Phase 1 acceptance for the 2026-08-18 capture; do not imply that flag-on fixes that turn.
2. Wire evaluation (and `QualityHit`) to the same unit/span/variant path as chat, or keep the flag off until that is done.
3. Stop putting translated variant text on public `RetrievalResult` unless persistence is on.
4. Add observe-only `MODIFIES` diagnostics, or document that first enable is a recall change.
5. Rename the stale-revision exclusion reason; add a REPLACES fixture.
6. Document that `document_id` + expansion returns modifiers.

Non-goals correctly left alone: web fetching, threshold retune, translated-dense admission, OCR/ingestion, GraphRAG, pre-rerank consolidation redesign.
