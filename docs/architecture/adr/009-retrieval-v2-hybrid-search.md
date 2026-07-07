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
