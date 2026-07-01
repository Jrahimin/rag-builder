# ADR-005: Taskiq for Background Job Processing

**Status:** Accepted (refined)  
**Date:** 2026-06-28 (updated 2026-06-30)

## Context

Long-running AI tasks must not block HTTP. A queue abstraction is needed before
implementing ingestion, embedding, or indexing pipelines.

## Decision

- Default queue backend: **Taskiq** (Redis-native, async-first, actively maintained)
- Application contract: `JobQueue.enqueue(JobDefinition) -> job_id` only
- `JobDefinition` carries `project_id`, `payload_version`, `idempotency_key`, `retry`
- `RetryPolicy` is the single retry source (no duplicate `max_attempts` fields)
- Worker handler registration belongs in the Taskiq adapter and worker modules, not the app interface

Implementation: `TaskiqJobQueue`, `app.worker.broker`, `app.worker.handlers.document`.

## Consequences

- API returns 202 + job_id pattern is standardized
- Separate worker process required in production (`taskiq worker ...`)
- One queue backend per deployment (no mixing Taskiq + Celery)
- Compatible with `redis-py` 7.x (platform pin; `taskiq-redis` requires `redis<8`)

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Arq | Maintenance-only; `redis-py<6` conflicts with platform Redis client |
| Celery default | Heavier infra; less async-native |
| In-process BackgroundTasks | Not durable; blocks scaling |
| Synchronous processing in HTTP | Explicitly forbidden |
| Rich queue interface upfront | No consumer yet; trimmed to `enqueue` |

## Supersedes

Originally accepted **Arq** (2026-06-28). Migrated to Taskiq when Arq's `redis-py`
pin blocked dependency resolution with the platform's Redis 8 client.
