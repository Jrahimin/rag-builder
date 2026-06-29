# Background Processing

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Model

```text
HTTP API  →  JobQueue.enqueue(JobDefinition)  →  202 Accepted
                        ↓
                 Redis + Arq worker (future)
                        ↓
              module job handler
```

## Application contract (foundation)

- `JobDefinition` — `name`, `project_id`, `payload_version`, `payload`, `idempotency_key`, `retry`
- `JobQueue.enqueue(job) -> job_id` — single application-facing method
- `RetryPolicy` — single source for retry settings (no duplicate `max_attempts`)

Worker handler registration belongs in the Arq adapter, not the application queue interface.

## Deferred

- Arq implementation
- Worker process / container
- Job status tracking API
- Cancellation

See ADR-005 and ADR-006.
