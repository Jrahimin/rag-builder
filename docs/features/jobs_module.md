# Durable Jobs

## Purpose

Make asynchronous document processing recoverable across API, Redis, and worker
interruptions while exposing product-level progress and retry controls.

## Architecture and flow

The originating module transaction stages its business mutation together with a
Project-scoped JobRun, content-addressed secret-free configuration snapshot, and
JobOutbox row. The application dispatcher delivers committed intents to Taskiq.
Workers acquire a database lease, heartbeat and report progress, and atomically
transition to success, retry, or terminal failure.

See [durable background processing](../architecture/background-processing.md)
for the failure and replay model.

## Data and API

- `job_runs`: execution identity, stage/progress, attempts, lease, timestamps,
  payload, document/configuration references, structured failure.
- `job_configuration_snapshots`: immutable normalized output-affecting settings,
  deduplicated within a Project by hash and containing no credentials.
- `job_outbox`: pending/dispatched Taskiq intent with dispatch retry metadata.
- [Jobs API](../api/jobs_api.md): Project-scoped list, detail, and failed-job retry.

## Correctness decisions

- PostgreSQL is execution truth; Redis is an at-least-once transport.
- Application retries are durable state transitions, not Taskiq middleware retries.
- Document lifecycle remains separate from worker execution state.
- Expected document version, advisory locks, idempotent child keys, and
  transactional output replacement make every stage replay-safe.
- Manual retry creates a linked new job and reuses the original configuration.

## Configuration

`APE_JOBS__DISPATCHER_*`, `APE_JOBS__LEASE_SECONDS`,
`APE_JOBS__HEARTBEAT_SECONDS`, `APE_JOBS__MAX_ATTEMPTS`, and the retry/dispatch
delay settings tune execution. Heartbeat must be shorter than the lease.

## Intentional non-goals

Cancellation, webhooks, UI, billing, new executor systems, and customer-facing
authorization are not part of this phase.
