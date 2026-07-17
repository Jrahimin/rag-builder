# Durable Background Processing

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Model

```text
HTTP service transaction
  ├─ business mutation
  ├─ immutable configuration snapshot
  ├─ project-scoped JobRun
  └─ JobOutbox dispatch intent
             │ commit
             ▼
DurableJobDispatcher → Redis / Taskiq → handler(job_id)
                                            │
                                  lease + heartbeat + progress
                                            │
                           succeeded | retry_scheduled | failed
```

PostgreSQL is the source of truth for execution. Redis/Taskiq is only the
executor transport. Upload, reprocess, embed, and index commit the business
change, job, and outbox row together, then make a best-effort immediate dispatch.
If Redis is unavailable, the API result remains committed and the dispatcher
retries the outbox with bounded exponential backoff.

## Persisted contracts

- `JobRun` is Project-scoped and records type, state, stage, progress, payload,
  idempotency key, attempt limits, lease/heartbeat, timestamps, document link,
  immutable configuration snapshot, and structured failure.
- `JobConfigurationSnapshot` stores normalized, secret-free processing/index
  configuration by content hash. Workers restore the snapshot while retaining
  live deployment secrets.
- `JobOutbox` stores one durable Redis dispatch intent. Claiming uses
  `FOR UPDATE SKIP LOCKED`; failed dispatch remains pending with `available_at`.
- `JobService` owns submission, inspection, lease transitions, retry scheduling,
  terminal transitions, outbox dispatch, and expired-lease recovery.

The stable job types are `document.process`, `document.embed`, and
`document.index`. Their states are `queued`, `running`, `retry_scheduled`,
`succeeded`, and `failed`.

## Worker correctness

Taskiq messages contain only the persisted job identity. A handler first
atomically acquires its lease; duplicate or stale delivery cannot acquire the
same run and is ignored. A separate session heartbeats the lease and updates
stage/progress during long work. Transient provider/database/connection failures
are retried by durable state; permanent failures become terminal structured
failures. Taskiq middleware does not own application retries.

Expired running leases are recovered by the application dispatcher. If attempts
remain, the same job is returned to `retry_scheduled` and receives a fresh
outbox intent; otherwise it becomes `failed` and the related Document receives
the safe terminal message.

Stage output is replay-safe:

- the expected `Document.version` fences obsolete work;
- a transaction-scoped advisory lock serializes each document/stage;
- chunks, active embedding-set rows, keyword rows, and BM25 statistics are
  replaced transactionally rather than appended;
- child jobs use stable configuration/version-aware idempotency keys;
- a stale worker cannot publish success after losing its lease.

`Document.status` remains the product lifecycle (`queued` through `ready` or
`failed`), not the worker admission or retry state.

## Product API

All endpoints are authenticated and Project-scoped:

- `GET /api/v1/projects/{project_id}/jobs`
- `GET /api/v1/projects/{project_id}/jobs/{job_id}`
- `POST /api/v1/projects/{project_id}/jobs/{job_id}/retry`

Existing asynchronous Document actions keep their status codes and response
shape and add nullable `job_id`. Manual retry creates a new JobRun linked by
`retry_of_job_id` and reuses the original immutable configuration snapshot.

## Runtime ownership

API composition and the dispatcher share `composition/jobs.py`. Taskiq handlers
own process-local database/provider construction through `worker/job_runtime.py`.
Retrieval wiring shared by API, workers, CLI, and tests remains in
`composition/retrieval.py`.

## Deferred

Cancellation, webhooks, UI, provider registries, extra queue systems, and
customer billing/entitlements are later-roadmap concerns.

Interactive chat generation remains synchronous inside the HTTP request; it is
not a durable ingestion/indexing job.
