# System — health probes

Unversioned infrastructure endpoints (not under `/api/v1`).

## GET /health/live

Liveness — process is running; does not probe dependencies.

**Response** `200`

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "service": "ape",
    "version": "0.9.0",
    "environment": "development"
  }
}
```

## GET /health/ready

Readiness — probes PostgreSQL (including the Alembic head, pgvector extension,
and configured `vector(n)` dimension), Redis, and configured object storage. Startup-only provider
capability results are included with `cached=true`; health requests never repeat LLM,
embedding, reranker, or OCR calls.

**Response** `200` when all dependencies reachable; `503` when degraded.

```json
{
  "success": true,
  "data": {
    "status": "ready",
    "service": "ape",
    "version": "0.9.0",
    "environment": "development",
    "dependencies": [
      { "name": "postgresql", "state": "ok", "detail": null, "latency_ms": 1.2, "cached": false },
      { "name": "llm_provider", "state": "ok", "detail": null, "latency_ms": 91.0, "cached": true }
    ]
  }
}
```

`GET /health` and `GET /ready` are temporary compatibility aliases and should
not be used by new integrations.

## GET /metrics

Admin-gated Prometheus-compatible current operational gauges. See
[operator_api.md](operator_api.md) for the JSON operator surfaces.
