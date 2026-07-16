# ADR-013: PostgreSQL-native semantic retrieval with pgvector

**Status:** Accepted  
**Date:** 2026-07-17

## Context

APE stored packed float vectors in PostgreSQL and copied them into Qdrant for
semantic search. The duplicate index introduced a second persistence boundary,
separate health and backup concerns, and eventual-consistency failure modes.
Project, document, embedding-version, and metadata filters already originate in
PostgreSQL.

## Decision

APE makes `chunk_embeddings` the only persistent semantic index and uses the
PostgreSQL `vector` extension through SQLAlchemy.

- Dense embeddings use a deployment-configured `vector(n)` column. Phase 1
  supports dimensions from 1 through 2,000; changing dimension is a deployment
  migration followed by re-embedding.
- Semantic ranking uses pgvector cosine distance and exposes
  `score = 1 - cosine_distance`.
- An HNSW index with `vector_cosine_ops` accelerates candidates. `hnsw.ef_search`
  is transaction-local and configurable.
- Every query constrains `project_id` and the active embedding set, provider,
  and model. Optional document and allowlisted metadata filters execute in SQL.
- Existing packed BYTEA rows are not converted. Their documents return to
  `chunked`, stale rows are removed, and normal embed/index jobs rebuild them.
- Customer PostgreSQL must provide `CREATE EXTENSION vector`, either to the
  migration role or through platform-operator provisioning.

Model-facing providers remain abstracted. pgvector is retrieval persistence,
not a model provider, so vendor SQL stays in the retrieval repository.

## Consequences

- PostgreSQL becomes the transactional source of truth for relational, lexical,
  and semantic retrieval state.
- Backups, deletion, Project isolation, and filtered search share one database.
- Deployments must pin a pgvector-capable PostgreSQL image or managed service.
- Dimension/model changes require an explicit migration and re-embedding
  rollout; they cannot silently share one HNSW column.
- The Qdrant projection, connectivity, configuration, package, and deployment
  service are removed.

## Alternatives considered

- **Keep Qdrant as a replica:** rejected because it preserves the duplicate
  persistence and reconciliation boundary.
- **Dual-write cutover:** rejected for this self-hosted phase; re-embedding is
  simpler and deterministic.
- **Unbounded `vector` without HNSW:** rejected because it cannot provide the
  selected fixed-dimension HNSW deployment contract.
- **Convert BYTEA in SQL:** rejected because packed-row provenance and dimension
  correctness cannot be established safely during migration.
