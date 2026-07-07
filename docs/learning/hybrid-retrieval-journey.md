# Hybrid Retrieval Journey

This guide walks through Retrieval v2: why hybrid search exists, how candidates
flow through the pipeline, and what to configure in production.

## Why hybrid?

Semantic (vector) search excels at paraphrase and conceptual similarity. Keyword
(BM25) search excels at exact tokens, codes, SKUs, and rare identifiers. Production
RAG uses **both**, then fuses rankings before optional reranking.

```text
Query
  ├── SemanticRetriever  → vector candidates
  └── KeywordRetriever   → BM25 candidates
            ↓
      RRF fusion (rank-based, not raw score blend)
            ↓
      Reranker (optional, bounded top_n window)
            ↓
      ResultHydrator (single PG hydration)
            ↓
      SearchResponse / Chat via RetrievalPort
```

## RetrievalContext

Every retriever receives one immutable `RetrievalContext`:

- `project_id`, `embedding_set_version`, filters, `top_k`, `strategy`
- Candidate pool sizes, RRF weights, rerank settings

Retrievers return **candidate hits only** (`chunk_id`, `score`, `source`). No
chunk/document ORM loading inside retrievers keeps the hot path fast.

## RRF instead of score normalization

Vector cosine, BM25, and reranker scores live on different scales. RRF combines
**rank positions**:

```text
fused = Σ weight_source / (k + rank_source)
```

Default `k` is configurable via `APE_RETRIEVAL__RRF_K` (60 is a common starting point).

## Keyword index versioning

Keyword rows are tagged with `embedding_set_version` even though BM25 does not use
embeddings. This keeps hybrid search on one coherent snapshot when you bump the
deployment embedding version and reindex.

## Configuration checklist

| Variable | Production starting point |
| -------- | ------------------------- |
| `APE_RETRIEVAL__STRATEGY` | `hybrid` |
| `APE_RETRIEVAL__SEMANTIC_CANDIDATE_TOP_K` | `50` |
| `APE_RETRIEVAL__KEYWORD_CANDIDATE_TOP_K` | `50` |
| `APE_RETRIEVAL__RERANK_ENABLED` | `true` |
| `APE_RETRIEVAL__RERANKER_BACKEND` | `lexical` (self-hosted) |

Use `strategy=semantic` on `POST /search` to compare against the Phase 3 baseline.

## Common mistakes

1. **Skipping reindex** — hybrid keyword path is empty until `document.index` runs.
2. **Treating `score` as cosine similarity** — final scores are fused/reranked relevance.
3. **Hydrating inside retrievers** — duplicates DB work and slows every candidate path.
4. **Unscoped keyword queries** — all repositories must filter by `project_id` and active `embedding_set_version`.

## Related

- [Retrieval module](../features/retrieval_module.md)
- [ADR-009](../architecture/adr/009-retrieval-v2-hybrid-search.md)
- [Retrieval API](../api/retrieval_api.md)
