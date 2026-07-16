# pgvector Operations Runbook

Use this runbook for the Qdrant-to-pgvector cutover, recovery, embedding model
changes, and routine HNSW operations. The application contract does not change:
documents still pass through `chunked → embedding → embedded → indexing → ready`.

## Preconditions

- Take a restorable PostgreSQL backup and record its timestamp.
- Confirm the target PostgreSQL version can install pgvector.
- Set `APE_EMBEDDING__DIMENSIONS` to the dimension used by the deployment.
- Keep API and workers stopped while applying the cutover migration.
- If the migration role cannot create extensions, have an operator run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## Deploy and verify the schema

Run from `backend/`:

```bash
alembic upgrade head
```

Then verify the extension, dimension, and HNSW index:

```sql
SELECT extversion FROM pg_extension WHERE extname = 'vector';

SELECT format_type(a.atttypid, a.atttypmod) AS embedding_type
FROM pg_attribute AS a
JOIN pg_class AS c ON c.oid = a.attrelid
WHERE c.relname = 'chunk_embeddings'
  AND a.attname = 'embedding'
  AND NOT a.attisdropped;

SELECT indexdef
FROM pg_indexes
WHERE indexname = 'ix_chunk_embeddings_embedding_hnsw_cosine';
```

Expected: one extension version, `vector(<configured dimension>)`, and an
`hnsw` index using `vector_cosine_ops`. `/ready` also fails with an actionable
message if the extension or dimension contract is wrong.

## Re-embed and reindex

The migration deliberately deletes legacy packed vectors and returns affected
documents to `chunked`. Do not convert packed bytes or copy old vector rows.

For a single document, call the unchanged endpoints:

1. `POST /api/v1/projects/{project_id}/documents/{document_id}/embed`
2. Wait for `embedded`.
3. `POST /api/v1/projects/{project_id}/documents/{document_id}/index`
4. Wait for `ready`.

For a controlled project-wide rebuild, use the operational CLI to reprocess
source documents and let the normal worker chain recreate chunks, embeddings,
and keyword rows:

```bash
python -m app.cli.reindex_cli project --project-id <uuid> --dry-run
python -m app.cli.reindex_cli project --project-id <uuid> --full
```

Keep workers scaled for the expected queue load. Repeated embed/index jobs are
idempotent for the same Project, document, provider, model, and embedding-set
version.

## Monitor rollout

```sql
SELECT status, count(*)
FROM documents
WHERE deleted_at IS NULL
GROUP BY status
ORDER BY status;

SELECT d.project_id,
       count(*) FILTER (WHERE d.status = 'ready') AS ready_documents,
       count(DISTINCT ce.document_id) AS documents_with_embeddings,
       count(DISTINCT cki.document_id) AS documents_with_keyword_rows
FROM documents AS d
LEFT JOIN chunk_embeddings AS ce
  ON ce.project_id = d.project_id AND ce.document_id = d.id
LEFT JOIN chunk_keyword_index AS cki
  ON cki.project_id = d.project_id AND cki.document_id = d.id
WHERE d.deleted_at IS NULL
GROUP BY d.project_id;
```

Investigate `failed`, long-running `embedding`, or long-running `indexing`
documents through structured worker logs before retrying them.

## Validate retrieval

- Run representative semantic and hybrid searches in every Project cohort.
- Confirm document and allowlisted metadata filters.
- Confirm a query against another Project returns no results.
- Delete a disposable document and confirm it immediately disappears from both
  semantic and hybrid search.
- Run the opt-in benchmark against a copied development database:

```bash
APE_RUN_PGVECTOR_BENCHMARKS=true \
APE_BENCHMARK_CORPUS_MULTIPLIER=100 \
pytest -m benchmark tests/benchmarks/test_pgvector_retrieval_benchmark.py
```

The harness records ingest documents/second, index-build p95, semantic p50/p95,
hybrid p95, recall@5, and filtered recall@5. Tune the `APE_BENCHMARK_*`
thresholds to the agreed customer corpus SLOs before production approval.

## HNSW maintenance and tuning

- Start with `APE_RETRIEVAL__HNSW_EF_SEARCH=100`; benchmark before changing it.
- Higher `ef_search` usually improves recall at the cost of query latency.
- Use `REINDEX INDEX CONCURRENTLY ix_chunk_embeddings_embedding_hnsw_cosine`
  after major data churn or when index diagnostics justify it.
- Run normal `VACUUM (ANALYZE)` maintenance so the planner has current filter
  statistics. Do not routinely rebuild HNSW without evidence.

## Model or dimension change

A fixed HNSW vector column accepts one dimension. Treat a dimension change as a
deployment migration:

1. Back up PostgreSQL and stop writers.
2. Bump `embedding_set_version` and configure the new model/dimension.
3. Ship an Alembic migration that recreates the vector column and HNSW index.
4. Re-embed and reindex all documents.
5. Validate retrieval and benchmark thresholds before reopening traffic.

Never truncate, pad, or silently coerce vectors.

## Backup, restore, and rollback

Use standard logical or physical PostgreSQL backup tooling; embeddings are part
of the same database backup as documents and keyword rows. Restore into a server
where pgvector is installed, apply migrations, and verify the schema queries
above before starting workers.

Migration downgrade also discards native vectors and returns affected documents
to `chunked`; it cannot reconstruct legacy packed bytes. A rollback therefore
requires the same re-embedding process, or restoration of the pre-cutover
database backup.
