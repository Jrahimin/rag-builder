# Operator — deployment operations

All routes require the deployment admin key when authentication is enabled. Responses are
sanitized and never contain secret values.

## GET /api/v1/operator/overview

Combined dependency, worker, metric, and recent-failure status.

## GET /api/v1/operator/dependencies

Cheap live infrastructure checks plus cached startup provider capability results.

## GET /api/v1/operator/workers

Active Taskiq workers, heartbeat age, process identity, version, and queue.

## GET /api/v1/operator/metrics

Job/queue/retry/failure, latency, token usage, corpus/storage, and active index-version metrics.

## GET /api/v1/operator/configuration

Allowlisted active runtime and provider configuration plus the most recent secret-free
configuration snapshot for each project.

## GET /api/v1/operator/failures

Recent terminal job failures. Query: `limit` (1–100, default 20).

## GET /api/v1/operator/audit-events

Recent immutable job audit events. Query: `limit` (1–200, default 50), `offset`.

## GET /metrics

Lightweight Prometheus-compatible current gauges. This unversioned scraper endpoint uses the same
admin-key gate.
