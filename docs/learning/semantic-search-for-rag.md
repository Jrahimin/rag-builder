# Semantic Search for RAG

Phase 3 exposes a **semantic retrieval baseline** — vector similarity search with metadata filters. This is the integration path for future Chat, not production hybrid retrieval.

## Flow

```text
POST /search
  → embed query (same provider as documents)
  → pgvector HNSW cosine search (project-scoped SQL)
  → hydrate chunks + document filename
  → RetrievalResult DTO
```

## RetrievalResult

Stable API shape for downstream Chat: `chunk_id`, `document_id`, `content`, `score`, `filename`, offsets, `metadata`.

## Metadata filters

Only keys in `APE_RETRIEVAL__FILTERABLE_METADATA_KEYS` are applied. Unknown
keys are ignored; sanitized values become predicates on joined
`document_chunks.metadata` rows.

Hybrid BM25, RRF, and optional reranking are implemented alongside the semantic
path. `ResultHydrator` remains the only response-content hydration point.

## Production considerations

- Tune `top_k` and optional `score_threshold` per deployment
- Monitor `search_complete` structured logs (`duration_ms`, `hit_count`)
- Deleted documents are excluded via ORM hydration checks
