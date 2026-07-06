# ADR-008: Chat on Semantic Retrieval Baseline

**Status:** Accepted  
**Date:** 2026-07-05

## Context

ADR-007 sequences retrieval delivery: semantic baseline first, hybrid (BM25 +
vector + RRF + reranker) as Retrieval v2. Chat completes the RAG user journey
(question → retrieve → prompt → LLM → grounded answer + citations) and must
respect module boundaries (`modules/conversations/` must not import
`modules/retrieval/` internals).

Interactive chat generation is user-facing and latency-sensitive; it differs
from ingestion workloads (OCR, embedding, indexing) that must run in background
workers.

## Decision

Ship **Chat v1** on the **semantic retrieval baseline**, with an explicit upgrade
path to Retrieval v2 behind a `RetrievalPort` adapter in the composition layer.

| Topic | Decision |
| ----- | -------- |
| Retrieval dependency | `RetrievalPort` Protocol; adapter maps `SearchService` today, hybrid later |
| Ranking | Owned by retrieval; chat `ContextBuilder` only dedupes and applies budgets |
| Generation execution | Synchronous inside HTTP request (JSON or SSE); no new background jobs |
| Transaction boundaries | **Two commits per turn** — persist user message and commit before retrieval/LLM; persist assistant after generation |
| Production upgrade | Swap retrieval adapter to hybrid v2 without changing chat module internals |

### Transaction model

```text
Tx1: persist user message + update last_message_at → commit
(no open transaction during retrieval + LLM)
Tx2: persist assistant (+ citations, diagnostics, auto-title) → commit
```

If the LLM fails after Tx1, the user message remains; no assistant row is written.

## Consequences

- Amends ADR-007 guidance: Chat v1 may ship on semantic baseline; hybrid remains
  the production upgrade behind the same port.
- `dependencies/conversations.py` owns cross-module wiring (retrieval adapter, LLM).
- Streaming (Phase 3) uses the same Tx1/Tx2 split; client disconnect cancels
  provider streams best-effort.

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Block Chat until Retrieval v2 | Delays E2E RAG learning milestone |
| Single transaction per turn | Holds DB transaction open during slow LLM calls |
| Import `SearchService` from chat module | Violates module boundary rules |
