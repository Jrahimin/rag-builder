# ADR-005: Arq for Background Job Processing

**Status:** Accepted (refined)  
**Date:** 2026-06-28

## Context

Long-running AI tasks must not block HTTP. A queue abstraction is needed before
implementing ingestion, embedding, or indexing pipelines.

## Decision

- Default queue backend: **Arq** (Redis-native, async-first)
- Application contract: `JobQueue.enqueue(JobDefinition) -> job_id` only
- `JobDefinition` carries `project_id`, `payload_version`, `idempotency_key`, `retry`
- `RetryPolicy` is the single retry source (no duplicate `max_attempts` fields)
- Worker handler registration belongs in the Arq adapter, not the app interface

Implementation (Arq adapter, worker process) deferred to Phase 1.

## Consequences

- API returns 202 + job_id pattern is standardized
- Separate worker process required in production
- One queue backend per deployment (no mixing Arq + Celery)

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Celery default | Heavier infra; less async-native |
| In-process BackgroundTasks | Not durable; blocks scaling |
| Synchronous processing in HTTP | Explicitly forbidden |
| Rich queue interface upfront | No consumer yet; trimmed to `enqueue` |
