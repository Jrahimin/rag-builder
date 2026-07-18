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
