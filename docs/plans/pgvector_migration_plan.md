# pgvector Migration Plan — PostgreSQL-Native Semantic Retrieval

**Status:** Complete — WP0 through WP5 implemented
**Scope:** Replace Qdrant with pgvector for all local and self-hosted APE deployments without changing the public ingestion, search, or chat contracts.

---

## Decision and target state

APE will use PostgreSQL for relational data, lexical retrieval, and dense-vector
retrieval. pgvector is enabled once per deployment database and the
`chunk_embeddings` table becomes the only persistent semantic index.

This is a **hard cutover**, not an optional Qdrant fallback. It removes a
separate replicated vector index, its health dependency, and its operational
burden. Provider abstraction remains for model-facing services (embeddings,
LLMs, rerankers, storage); semantic retrieval becomes a retrieval persistence
concern because it shares the request/worker transaction and SQL filters.

```text
Before
chunks → embeddings in PostgreSQL (BYTEA) → Qdrant points → semantic search

After
chunks → embeddings in PostgreSQL (vector) ────────────→ semantic search
                       └── PostgreSQL FTS/BM25 ─────────→ hybrid RRF/rerank
```

### Non-goals

- No public API path, request, response, document status, or job-name change.
- No change to the Knowledge → Retrieval module boundary.
- No change to the existing hybrid policy: semantic candidates + PostgreSQL
  BM25 candidates → RRF → optional reranker.
- No automatic conversion of existing packed `BYTEA` vectors. Existing ready
  documents are re-embedded and re-indexed after cutover.
- No Qdrant compatibility mode or dual-write period in normal application code.

---

## Why this fits APE

The existing system already treats PostgreSQL as the source of truth:
`EmbeddingWorkflow` writes `chunk_embeddings`, while
`VectorIndexingWorkflow` copies the same vectors and selected metadata into
Qdrant. The copy is an extra failure/reconciliation boundary. pgvector makes
the semantic lookup transactional with the embedding data and lets Project,
Document, embedding-set-version, and metadata filters remain relational.

The current contracts are a useful seam, but the Qdrant-shaped operations
(`ensure_collection`, point payloads, remote purge) do not model a SQL-backed
index accurately. Do not implement pgvector as a mostly no-op Qdrant provider.
Instead, make the semantic-index repository session-aware and keep vendor SDK
interfaces outside the retrieval module.

---

## Invariants and acceptance criteria

| Area | Required outcome |
| ---- | ---------------- |
| API compatibility | Existing document embed/index endpoints and `POST /search` retain their schemas and status semantics. |
| Isolation | Every semantic query constrains `project_id`; document, embedding-set-version, and allowlisted metadata filters remain enforced in SQL. |
| Consistency | A document reaches `ready` only after its native vector rows and keyword rows are transactionally persisted. |
| Search | Cosine similarity, `score_threshold`, semantic-only mode, hybrid RRF, reranking, and result hydration keep their present behavior. |
| Operations | `docker compose up --build` needs no Qdrant service; host-venv and Docker use the same pgvector-enabled database contract. |
| Recovery | Existing documents can be re-embedded/re-indexed idempotently; no data loss when rerunning jobs. |
| Tests | Unit tests stay external-service-free; integration tests exercise actual pgvector, not the in-memory store. |

---

## Design choices to settle before implementation

### 1. Embedding type and dimension policy

Use cosine distance and create an HNSW index. Start with `vector(1536)` only
if APE deliberately standardizes its production embedding dimension; otherwise
use a dimension strategy that matches the supported providers before writing
the migration.

Recommended Phase 1 policy:

- Support `vector(n)` for dimensions up to 2,000.
- Reject an indexed embedding backend/model whose dimensions differ from the
  deployment's configured vector dimension.
- Retain `embedding_set_version`, provider, and model audit columns.
- Defer `halfvec`/multi-dimension expression indexes until a real provider
  requires them; do not silently allow the current 4,096-dimension maximum.

This explicit policy matters because a pgvector HNSW index has a fixed vector
dimension. A model change is therefore a deployment migration: configure the
new dimension, recreate the vector index/column as appropriate, then re-embed
and re-index documents.

### 2. Index and filter strategy

Create:

- HNSW cosine index on `chunk_embeddings.embedding`.
- B-tree index for the selective relational filters: at minimum `project_id`,
  `embedding_set_version`, provider, model, and document id where query plans
  justify it.
- GIN index on `document_chunks.chunk_metadata` only if metadata filtering is
  part of the deployed workload. Keep the existing allowlist; never expose
  arbitrary JSON predicates through the API.

Semantic SQL must set pgvector search options with `SET LOCAL` in the existing
request/worker transaction and apply metadata/project filters inside the
candidate query. Benchmark `hnsw.ef_search` and iterative scans with realistic
per-Project corpus sizes before selecting production defaults.

### 3. Persistence port

Introduce a retrieval-owned `PgVectorEmbeddingRepository` (or extend
`ChunkEmbeddingRepository`) with these responsibilities:

- bulk insert/upsert native embedding rows;
- delete rows by Project/document/version in the current session;
- query top-k cosine candidates with Project/document/version/metadata filters;
- return neutral `CandidateHit` fields (`chunk_id`, score, metadata).

`SemanticRetriever` calls this repository after embedding the query. It does
not import pgvector or SQLAlchemy expressions directly. `ResultHydrator`,
`KeywordRetriever`, `HybridRetriever`, the reranker contract, and the Chat
`RetrievalPort` remain unchanged.

---

## Work packages

### WP0 — Architecture and compatibility contract

1. Add an ADR recording the Qdrant → pgvector hard cutover, the fixed-dimension
   policy, HNSW/cosine choice, and re-embedding rollout.
2. Update architecture, provider, deployment, system, and domain-ownership
   documents to say semantic retrieval is PostgreSQL-native.
3. Update `docs/features/retrieval_module.md`, API docs, learning journeys, and
   plan index. Archive or rewrite `vector-storage-and-qdrant.md` as pgvector
   material; do not leave Qdrant presented as an active dependency.
4. Record the supported deployment posture: pgvector is required in customer
   PostgreSQL; managed PostgreSQL must support `CREATE EXTENSION vector` or have
   it provisioned by the platform operator.

**Gate:** documentation names one source of truth and contains no active
Qdrant deployment instructions.

### WP1 — Database image, dependency, and migration foundation

1. Replace `postgres:16-alpine` in Compose with a pinned pgvector PostgreSQL 16
   image. Preserve the existing `postgres_data` volume path and health check.
2. Remove `qdrant-client`; add the pinned Python `pgvector` package compatible
   with SQLAlchemy and asyncpg.
3. Add an Alembic revision that:

   - enables `vector` (`CREATE EXTENSION IF NOT EXISTS vector`);
   - adds the native embedding column and required indexes concurrently where
     deployment tooling allows it;
   - preserves audit/version columns and foreign keys;
   - marks pre-cutover rows for re-embedding rather than attempting unsafe
     SQL conversion from packed bytes.

4. Add a verified startup/migration preflight that fails with an actionable
   message when the extension is unavailable.
5. Update disposable integration-test database setup so it creates the
   extension before retrieval tests migrate.

**Gate:** a clean database and a migrated development database both complete
`alembic upgrade head`; pgvector extension and HNSW index exist as expected.

### WP2 — Native embedding persistence and semantic search

1. Update `ChunkEmbedding` and `EmbeddingWorkflow` to persist Python float
   lists into the native pgvector column; remove `vector_codec.py` once no
   migration or compatibility code needs it.
2. Extend the chunk-embedding repository with native top-k cosine search and
   bulk persistence operations.
3. Refactor `SemanticRetriever` to use the repository and return the same
   `CandidateHit` contract, including `score = 1 - cosine_distance` and the
   current threshold semantics.
4. Preserve the metadata allowlist by joining `document_chunks` and applying
   only `RetrievalContext.sanitized_metadata_filter()` values.
5. Keep `ResultHydrator` as the only point that loads response content and
   document details after semantic/hybrid candidate selection.

**Gate:** semantic search returns the same candidates, score ordering, Project
isolation, document filter, and allowed metadata behavior in integration tests.

### WP3 — Simplify indexing, lifecycle, and deletion

1. Remove Qdrant projection calls from `VectorIndexingWorkflow`; rename it to
   reflect its remaining role if that makes ownership clearer (for example,
   `RetrievalIndexingWorkflow`).
2. Keep the existing `chunked → embedding → embedded → indexing → ready`
   lifecycle and `document.embed` / `document.index` jobs. The index job now
   writes/rebuilds the PostgreSQL keyword index and marks the document ready;
   it does not make a remote vector call.
3. Simplify `RetrievalCleanupService`: delete embedding and keyword rows in the
   current session. Remove best-effort remote-vector purge and its failure log.
4. Delete Qdrant configuration, connectivity object, application lifespan
   wiring, readiness dependency, and `BaseVectorStoreProvider` only after all
   callers have moved to the repository boundary. Keep the in-memory helper
   only if it still adds unit-test value.

**Gate:** a deleted document is immediately absent from semantic and hybrid
search without an out-of-band cleanup operation; all lifecycle/idempotency
tests pass unchanged at the API boundary.

### WP4 — Local, Docker, and self-hosted deployment alignment

1. Remove the Qdrant service, named volume, host ports, environment variables,
   health check, worker dependency, and backend readiness configuration from
   `docker-compose.yml` and `.env.docker.example`.
2. Keep the existing one-shot `migrate` service. It must wait for PostgreSQL
   with pgvector, apply the extension migration, and complete before API and
   worker begin.
3. Update the backend/worker Docker configuration to remove Qdrant settings and
   client dependency. No application container needs special pgvector binaries.
4. Make host-venv instructions use the same pgvector-enabled PostgreSQL image
   or a locally installed extension; provide a quick verification command
   (`SELECT extversion FROM pg_extension WHERE extname = 'vector'`).
5. Update `/ready` to report PostgreSQL, Redis, and MinIO only. PostgreSQL
   readiness covers the native vector index because it lives in that database.
6. For self-hosted production, document extension ownership, backup/restore,
   HNSW index maintenance, and re-embedding rollout. Remove Qdrant from the
   customer-owned infrastructure list and topology diagrams.

**Gate:** full Docker Compose and hybrid host-API mode start successfully from
an empty volume; `/ready` is healthy; a document completes automatically to
`ready` and is searchable.

### WP5 — Test, performance, and rollout verification

1. Replace Qdrant-specific config, health, mock, and integration markers.
2. Add pgvector integration coverage for extension migration, HNSW index,
   semantic ranking, Project isolation, document/version filters, allowed
   metadata filters, deletion, re-embedding, and hybrid RRF.
3. Retain isolated unit tests with a fake/repository test double; they must not
   require Docker or PostgreSQL.
4. Add a benchmark fixture with representative documents and Projects. Measure
   ingest throughput, index build time, semantic p50/p95, recall@k, filtered
   recall, and hybrid latency for the intended customer corpus size.
5. Supply an operator runbook: backup PostgreSQL, deploy migration, confirm the
   extension/index, enqueue re-embed/re-index, monitor completion/failures,
   validate sample searches, then remove legacy packed-vector data if retained
   temporarily.

**Gate:** benchmark acceptance thresholds are agreed before production rollout;
the recovery runbook is tested against a copied development database.

---

## Module-by-module impact map

| Area | Change |
| ---- | ------ |
| `models/chunk_embedding.py` | Native pgvector column and index-compatible dimension policy replace `BYTEA`. |
| `modules/retrieval/repositories/` | Own native persistence and cosine candidate query. |
| `EmbeddingWorkflow` | Persists native vectors; retains batching, audit fields, and status transition. |
| `SemanticRetriever` | Uses retrieval repository, preserves neutral candidates and scores. |
| `VectorIndexingWorkflow` | Becomes keyword/index-finalization work; no remote vector upsert. |
| `KeywordRetriever`, `HybridRetriever`, RRF, reranker, hydrator | No contract change. |
| `RetrievalCleanupService` | Database-only deletion; no best-effort vector purge. |
| `IndexingService`, worker handlers, job registry | Preserve jobs and lifecycle; remove vector-store dependency injection. |
| `dependencies/retrieval.py`, `dependencies/knowledge.py` | Wire repository/session instead of vector-store singleton. |
| `main.py`, health service, common dependencies | Remove Qdrant client lifecycle and readiness check. |
| `platform/providers/` | Remove Qdrant implementation/factory/contract after callers migrate; embedding/storage/LLM abstractions stay. |
| `docker-compose.yml`, env examples, requirements | Use pgvector-enabled PostgreSQL; remove Qdrant package/service/configuration. |
| Tests/docs | Replace Qdrant assumptions with pgvector migration/search/deployment coverage. |

---

## Rollout sequence

```text
WP0 decision/docs
  → WP1 extension + schema + clean-environment proof
  → WP2 native persistence + semantic-search parity
  → WP3 lifecycle/delete simplification
  → WP4 Docker/local/self-hosted cutover
  → WP5 integration benchmark + re-embedding runbook
  → production deployment and Qdrant decommission
```

Production cutover is complete only when all ready documents have current
native embeddings and keyword rows, representative semantic and hybrid queries
pass validation, and the Qdrant service/data are no longer referenced by code,
Compose, configuration, health checks, or operational documentation.

## Implementation outcome

- `RetrievalIndexingWorkflow` preserves the public index job while finalizing
  native embeddings and PostgreSQL keyword/BM25 state transactionally.
- Search candidates require ready, non-deleted documents and enforce Project,
  version, provider/model, document, and allowlisted metadata filters in SQL.
- Document deletion removes native vector and keyword rows and rebuilds affected
  BM25 statistics in the document transaction.
- Compose, runtime configuration, health, packages, and application wiring have
  no Qdrant dependency.
- Real-pgvector integration tests cover schema/index creation, lifecycle
  visibility, ranking/thresholds, isolation, filters, deletion, re-embedding,
  idempotency, and hybrid search. `tests/benchmarks/` provides the opt-in SLO
  harness; production thresholds remain deployment-specific and must be agreed
  during the runbook rehearsal.
