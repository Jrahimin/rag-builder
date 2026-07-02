# ADR-007: Staged Retrieval Delivery

**Status:** Accepted  
**Date:** 2026-06-30

## Context

The platform architecture targets **hybrid retrieval** (BM25 + vector + RRF +
reranking) as the production-grade search path. Implementing hybrid, reranking,
and observability in one step would delay learning milestones and block Chat
integration on a large surface area.

Retrieval must also respect module boundaries: knowledge ingestion ends at
`chunked`; embedding, vector indexing, and query live in `modules/retrieval/`.

## Decision

Deliver retrieval in **staged phases** with explicit completion wording:

| Milestone | Scope | Production-grade? |
| --------- | ----- | ----------------- |
| **Retrieval Phase 3 baseline** | Semantic search API E2E (`ready` → `POST /search`) | No — learning + integration baseline |
| **Retrieval v2** | Hybrid BM25 + vector + RRF + reranker | Yes — required before Chat |

Phase 3 ships **semantic retrieval baseline only**:

- `SemanticRetriever` + `SearchService` (no `BaseRetriever` ABC until v2)
- Metadata filters on an allowlisted key set
- Hybrid BM25, RRF, and reranker explicitly deferred

This does **not** relax the long-term architecture goal of hybrid retrieval for
production workloads; it sequences delivery so each phase is testable and
documented.

## Consequences

- ADR-007 and feature docs must state hybrid is the **next** milestone after
  semantic baseline.
- Chat module should depend on Retrieval v2 for production RAG, not Phase 3
  baseline alone.
- `docs/plans/retrieval-module.md` is the implementation plan; module-architecture
  reflects the knowledge/retrieval split.

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Ship hybrid in Phase 3 | Too large; delays E2E semantic path and learning docs |
| Semantic-only as permanent v1 | Conflicts with production hybrid requirement in architecture |
| Embed inside knowledge workflow | Violates module boundary; couples ingestion to retrieval |
