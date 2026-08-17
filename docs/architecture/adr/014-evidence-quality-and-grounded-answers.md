# ADR-014: Persisted Evidence Quality and Deterministic Grounding

**Status:** Accepted  
**Date:** 2026-07-18

## Decision

Store immutable Project-scoped evaluation datasets and append-only run results in PostgreSQL. Run
them through the durable job/outbox runtime. Keep evaluation metrics in `modules/evaluation/`, and
reuse retrieval and conversation behavior through composition-layer ports.

Grounded chat retains `content` and `citations`, adds persisted claim-to-source mappings, and treats
insufficient evidence as a deterministic pre-generation outcome. Learned rerankers require a stored
same-dataset comparison and cannot be promoted unless retrieval gain, grounded-answer effect,
latency, operational fit, and failure behavior meet configured thresholds.

## Why

Offline scripts and prompt-only refusals cannot identify which configuration produced a result or
guarantee that unsupported questions decline. Persisted versions and an indexed-corpus fingerprint
make regressions auditable;
pre-generation refusal prevents a successful generator from fabricating an answer when retrieval is
not sufficient.

## Consequences

- Evaluation is asynchronous and shares existing job reliability semantics.
- A queued run fails before querying if its captured indexed-corpus fingerprint has changed.
- The operator console reads the same Project-scoped artifacts as the API.
- Claim grounding is conservative lexical evidence validation, not an entailment model.
- Hash embeddings remain valid for development but make embedding rerankers ineligible as learned
  candidates.
- Phase 5 corpus and index lifecycle work remains separate.

## Amendment: cross-language evidence semantics (2026-08-17)

The pre-generation gate separates ranking from evidence confidence. Only calibrated
`semantic_score` may reject evidence. Lexical query coverage is supplementary and rescue-only:
above a lower semantic floor, strong exact-term coverage may admit a candidate, but lexical
coverage can never veto semantic relevance. No runtime language or script detector participates in
the decision.

Claim validation remains deterministic and does not add claim embeddings or claim entailment.
Claims use three states:

- `supported` — lexical evidence clears the configured threshold;
- `unverified` — a structurally valid citation exists but there is no shared vocabulary for the
  lexical validator to inspect;
- `unsupported` — no valid evidence exists, or applicable lexical evidence falls short.

The compatibility `grounded` flag now means that no claim is `unsupported`. Evaluation reports
`unverified_claim_rate`, per-language-pair recall/nDCG, false-refusal rate, and false-accept rate.
False accepts are a hard regression guard: cross-language support must not be obtained by weakening
the refusal boundary.

New messages persist `best_semantic_evidence_score` as calibrated cosine similarity. Historical
rows may still carry `evidence_best_score`, which mixed ranking-scale and cosine-scale values;
that key is no longer written. Interpret historical `grounded` and score fields through the
configuration snapshot captured with the message.

## Amendment: candidate-local and passage evidence (2026-08-17)

Semantic acceptance and lexical rescue now evaluate one candidate/evidence unit at a time. The gate
cannot combine cosine from one chunk with vocabulary from another. Whole-chunk pgvector cosine
retains the `semantic_score` name and remains the default.

Bounded overlapping passage cosine is an optional, separately named evidence method
(`passage_semantic_score`, `bounded_token_max_v1`). It uses the active embedding provider and the
hybrid query vector, never a reranker score. Promotion requires separate positive/hard-negative
calibration, zero accepted-without-relevant-evidence cases, and an explicit conversation snapshot
with `evidence_score_mode=passage_max`.
