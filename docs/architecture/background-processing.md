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
- Worker process — `taskiq worker app.worker.broker:broker app.worker.handlers.document`
- `document.process` job — parse + chunk workflow

## Deferred

- Job status tracking API
- Cancellation
- Retry middleware wiring from `RetryPolicy`

See ADR-005 and ADR-006.
