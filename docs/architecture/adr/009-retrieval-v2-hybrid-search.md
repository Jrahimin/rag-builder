# ADR-009: Retrieval v2 Hybrid Search

**Status:** Accepted  
**Date:** 2026-07-06

## Context

ADR-007 staged retrieval delivery: semantic baseline first, hybrid (BM25 + vector +
RRF + reranker) as Retrieval v2. ADR-008 shipped Chat v1 on the semantic baseline
behind `RetrievalPort`, with hybrid as the production upgrade path.

The semantic baseline couples vector search with chunk hydration inside
`SemanticRetriever`. Production hybrid retrieval needs candidate-only retrievers,
one-time hydration, rank fusion, and an extensible reranker contract without
breaking `SearchResponse`, `RetrievalResult`, or `RetrievalPort`.

## Decision

Ship **Retrieval v2** as the production retrieval path with:

| Topic | Decision |
| ----- | -------- |
| Keyword index | PostgreSQL `chunk_keyword_index` + `keyword_term_stats`, versioned by `embedding_set_version` |
| Retriever input | Immutable `RetrievalContext` passed to every retriever |
| Retriever output | `CandidateHit` only (`chunk_id`, `score`, `source`, `metadata`) — no ORM hydration in retrievers |
| Hydration | `ResultHydrator` runs once after fusion/rerank |
| Fusion | Reciprocal Rank Fusion (RRF) on rank positions, not raw score normalization |
| Reranker | `BaseRerankerProvider.rerank(request: RerankRequest)` for future multimodal/batch support |
| First reranker | `LexicalRerankerProvider` (self-hosted token overlap; no external API) |
| Strategy toggle | `APE_RETRIEVAL__STRATEGY`: `semantic` \| `hybrid`; optional per-request override |
| Indexing | Existing `document.index` job refreshes vector points **and** keyword rows |
| Chat contract | `RetrievalPort` / `ContextChunk` unchanged; adapter-only wiring |

### Score semantics

- `RetrievalResult.score` is the **final ranking score**, not raw cosine similarity.
- With reranker disabled: fused RRF score.
- With reranker enabled: reranker relevance score; fused score as tie-breaker.

## Amendment: calibrated evidence and multilingual ranking (2026-08-17)

`RetrievalResult.score` remains a ranking score only. Candidate and result DTOs now also carry
`semantic_score`, always `1 - cosine_distance` against the active build. Keyword-only RRF
candidates receive that score through a bounded pgvector lookup using the already-computed query
vector. RRF and reranker scores must never be interpreted as semantic confidence.

The production default remains hybrid BM25 + dense retrieval + RRF. The rerank stage stays
enabled; its occupant is the `noop` pass-through so fused RRF order is retained,
`rerank_status=passthrough`, and `score_scale=reciprocal_rank_fusion` is declared without a
content-load round-trip. The lexical reranker remains an
offline/provider-free comparison and uses query coverage instead of length-biased Jaccard.
A true multilingual cross-encoder may replace the default only after a persisted quality and
latency comparison; re-sorting by the same bi-encoder similarity is not treated as reranking.

The dense baseline is `text-embedding-3-large` at 1024 dimensions, embedding set version 2.
Migration `0026_multilingual_embeddings` changes the fixed pgvector contract and deliberately
invalidates old retrieval builds.

### Production default

- `.env.example` documents `APE_RETRIEVAL__STRATEGY=hybrid`.
- Code default remains `semantic` so existing tests stay stable without env overrides.

## Consequences

- Amends ADR-007: Retrieval v2 is the production retrieval milestone.
- Existing `ready` documents require reindex (`POST .../index`) to populate keyword rows.
- Chat diagnostics read deployment strategy from `RetrievalConfig` (metadata only).
- `docs/features/retrieval_module.md`, `docs/api/retrieval_api.md`, and learning docs updated.

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Qdrant sparse vectors | Stronger vendor coupling; deferred |
| OpenSearch | New infrastructure for v2 scope |
| `ts_rank_cd` only | Not true BM25; weakens production path |
| `rerank(query, candidates)` | Does not scale to multimodal/batch reranking |
