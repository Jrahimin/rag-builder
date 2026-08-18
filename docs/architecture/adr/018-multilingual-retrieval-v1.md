# ADR-018: Multilingual Retrieval Version 1

**Status:** Accepted  
**Date:** 2026-08-18

## Context

English queries against Bangla OCR/legal chunks can retrieve the right passage at a weak rank
while a nearby wrong tax chunk ranks higher. Whole-chunk cosine for the observed gazette case is
about `0.13–0.18` on the relevant table and `0.20–0.28` on hard negatives. The production hybrid
path fused only original dense + original BM25, and the evidence gate could only admit on original
cosine (or out-of-scope passage scoring). Lowering `0.35` would admit those hard negatives.

## Decision

Keep `text-embedding-3-large` at 1024 dimensions and one PostgreSQL/pgvector index. Original
chunks remain the only evidence and citation source. Query translation is a retrieval artifact.

| Topic | Decision |
| ----- | -------- |
| Query translation | At most one target-language rewrite, default model `gpt-5-nano`, env-selectable `gpt-5-mini` |
| Original branches | Always run original dense and original lexical; language inference must not suppress them |
| Translated branches | Dense + lexical against `target OR mixed OR unknown` |
| Fusion | Equal-weight RRF with branch provenance |
| Reranker | One managed multilingual reranker (`Cohere rerank-v4.0-fast`); original query vs original passages |
| Evidence | When rerank is applied, calibrated reranker relevance is the only learned gate; original cosine is fallback |
| Builds | Language inventory is frozen on the immutable index-build manifest; no second vector index |

## Consequences

- Amends ADR-009: hybrid production path may execute translated branches before RRF.
- Amends ADR-014: after a true multilingual reranker is applied and calibrated, its relevance score
  is the candidate-local evidence signal. Cosine remains the fallback when rerank is passthrough
  or unavailable.
- Enabling language routing requires a new immutable reindex. Translation and Cohere stay off
  until gazette hard gates pass.
- Recall/MRR/nDCG numbers are promotion targets, not V1 ship blockers.

## Amendment: hosted_managed Cohere retrieval stack (2026-08-19)

The preferred production profile is `hosted_managed`:

- Embedding: Cohere `embed-v4.0` at 1024 dimensions with vendor-neutral `QUERY`/`DOCUMENT`
  purpose (`search_query` / `search_document` stay inside the Cohere adapter).
- Reranker: Cohere `rerank-v4.0-pro` after RRF. Platform default mode is Always. Projects may
  inherit or opt down to Cross-language or Off. Cross-language skips the paid call when inventory
  says query and corpus share a language. Missing key or API failure degrades to RRF + cosine.
- Generation: OpenAI `gpt-5.6-luna`. Conditional query translation remains at most one
  `gpt-5-nano` rewrite; the original query always runs.
- `embedding_set_version=3` for this stack. Never mix OpenAI and Cohere vectors in one active
  set. Cutover uses the existing immutable rebuild → validate → activate path. Query embeddings
  follow the active build identity. Live settings are the next-build target. Unlabeled or mixed
  identity is a configuration error, not an empty hit list. Rollback restores the retained
  build's provider/model/dimensions. Keep previous provider credentials until rollback is retired.
- `hosted_openai` remains a deprecated compatibility profile (OpenAI LLM + OpenAI embeddings)
  and must not require Cohere.

Post-generation per-claim LLM translation is removed. Claim/evidence cosine uses claim=`QUERY`
and evidence=`DOCUMENT`. The hosted_managed evidence gate ships `observe` until a small existing
Test Lab smoke confirms the pro-reranker threshold, then examples promote to `enforce`. Do not
treat `0.40` as already calibrated for embed-v4 + rerank-v4.0-pro.

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Lower `0.35` | Admits measured hard negatives |
| Passage-level evidence | Already measured negative margin / high latency |
| One translation per corpus language | Exceeds the V1 one-call cap |
| Suppress original English BM25 | Hides numeral/abbreviation overlap and changes today's hybrid contract |
| Embedding-model migration | Out of V1 scope |
