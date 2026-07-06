# Vector Storage and Qdrant

Phase 2 copies embeddings from PostgreSQL into Qdrant for approximate nearest-neighbor search.

## Collection design

- Single collection (`ape_chunks` by default)
- Point ID = `chunk_id` (stable across re-index)
- Payload: `project_id`, `document_id`, `chunk_index`, `embedding_set_version`, plus allowlisted chunk metadata

## Indexing flow

```text
embedded → POST /index → VectorIndexingWorkflow
  → delete stale points → upsert new points → status=ready
```

## Delete consistency

Document delete triggers best-effort Qdrant purge by `(project_id, document_id)` filter. Failures log `vector_purge_failed` without blocking the API response.

## Provider seam

`BaseVectorStoreProvider` keeps Qdrant SDK code in `platform/providers/implementations/`. Connectivity health checks remain in `platform/infra/connectivity/qdrant.py`.

## Alternatives

| Option | Notes |
| ------ | ----- |
| pgvector only | Fewer moving parts; slower at scale |
| Per-project collections | Stronger isolation; higher operational overhead |
| Embedded FAISS | Good for single-node; less cloud-native |
