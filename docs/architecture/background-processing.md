# Background Processing

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Model

```text
HTTP API  →  JobQueue.enqueue(JobDefinition)  →  202 Accepted
                        ↓
                 Redis + Taskiq worker
                        ↓
              module job handler
```

## Application contract (foundation)

- `JobDefinition` — `name`, `project_id`, `payload_version`, `payload`, `idempotency_key`, `retry`
- `JobQueue.enqueue(job) -> job_id` — single application-facing method
- `RetryPolicy` — single source for retry settings (no duplicate `max_attempts`)

Worker handler registration belongs in the Taskiq adapter and worker modules, not the application queue interface.

## Implemented

- `TaskiqJobQueue` — enqueues via Redis list broker (`ListQueueBroker`)
- Worker process — `python worker.py` (document + embedding + indexing handlers)
- `document.process` / `document.embed` / `document.index` jobs
- Retries — `RetryPolicy` (`max_attempts`, `initial_delay_seconds`) is translated to
  Taskiq `SmartRetryMiddleware` labels at dispatch (`platform/jobs/registry.py`);
  backoff shape (exponent, jitter, max delay) is configured on the broker in
  `app/worker/broker.py`

## Deferred

- Job status tracking API
- Cancellation (worker jobs)

See ADR-005 and ADR-006.

## Not background-processed

Interactive **chat generation** (non-stream and SSE) runs synchronously inside the
HTTP request. Services commit the user message before retrieval/LLM I/O and persist
the assistant message after generation (ADR-008). This is distinct from ingestion
and indexing workloads that must use the job queue.
