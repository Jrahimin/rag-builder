# Semantic Search for RAG

Phase 3 exposes a **semantic retrieval baseline** — vector similarity search with metadata filters. This is the integration path for future Chat, not production hybrid retrieval.

## Flow

```text
POST /search
  → embed query (same provider as documents)
  → vector store ANN search (project-scoped)
  → hydrate chunks + document filename
  → RetrievalResult DTO
```

## RetrievalResult

Stable API shape for downstream Chat: `chunk_id`, `document_id`, `content`, `score`, `filename`, offsets, `metadata`.

## Metadata filters

Only keys in `APE_RETRIEVAL__FILTERABLE_METADATA_KEYS` are applied (default: `source`, `tags`). Unknown keys are ignored to prevent arbitrary payload querying.

## What is deferred (Retrieval v2)

- BM25 keyword index
- Reciprocal rank fusion (RRF)
- Cross-encoder reranking
- `BaseRetriever` / `HybridRetriever` ABC

See [ADR-007](../architecture/adr/007-staged-retrieval-delivery.md).

## Production considerations

- Tune `top_k` and optional `score_threshold` per deployment
- Monitor `search_complete` structured logs (`duration_ms`, `hit_count`)
- Deleted documents are excluded via ORM hydration checks
