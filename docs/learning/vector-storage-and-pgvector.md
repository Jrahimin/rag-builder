# Vector Storage and pgvector

APE stores and searches dense embeddings in PostgreSQL. The
`chunk_embeddings.embedding` column is a fixed-dimension pgvector `vector(n)`
and is the semantic source of truth.

## Schema and index

- `vector(n)` dimension comes from `APE_EMBEDDING__DIMENSIONS` when the migration runs.
- Phase 1 supports at most 2,000 dimensions.
- Audit columns retain provider, model, provider version, content hash, document
  version, and `embedding_set_version`.
- HNSW uses `vector_cosine_ops`; semantic scores are `1 - cosine_distance`.
- A composite B-tree index supports Project/version/provider/model/document
  scoping. Chunk metadata has a GIN index for the allowlisted filter workload.

## Persistence and search flow

```text
document chunks
  → embedding provider
  → EmbeddingWorkflow writes Python float lists to vector(n)
  → SemanticRetriever embeds the query
  → ChunkEmbeddingRepository applies scoped SQL filters + cosine top-k
  → ResultHydrator loads response content and document details once
```

All semantic SQL constrains `project_id`. It also filters the active embedding
set, provider, and model, plus optional `document_id` and sanitized metadata.
`hnsw.ef_search` is set locally in the request transaction so one search cannot
change another connection's behavior.

Candidates also require a non-deleted document at `ready`. Native vectors are
written during `embedding`, but do not become visible to search until the index
job transaction has rebuilt keyword/BM25 rows and committed the ready status.

## Dimension changes and re-embedding

An HNSW-indexed vector column has one dimension. A model dimension change is a
deployment operation: back up PostgreSQL, change the configured dimension,
migrate/recreate the native column and index, then re-embed documents. APE does
not coerce or truncate vectors.

The initial cutover deliberately discards packed BYTEA embedding rows and moves
their documents back to `chunked`; the normal embed/index jobs rebuild them.

## Deployment requirement

PostgreSQL must have the `vector` extension installed. The migration runs
`CREATE EXTENSION IF NOT EXISTS vector`; managed services may require an
operator to provision it first. Verify with:

```sql
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

See [ADR-013](../architecture/adr/013-postgresql-native-semantic-retrieval.md).
For rollout and recovery, see the
[pgvector operations runbook](./pgvector-operations-runbook.md).
