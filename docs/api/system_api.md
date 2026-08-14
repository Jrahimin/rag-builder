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
and configured `vector(n)` dimension), Redis, configured S3-compatible object
storage, and ClamAV. Cached provider capability results are included with
`cached=true`; health requests never repeat LLM, embedding, reranker, or OCR
calls.

**Response** `200` when all core dependencies are reachable; `503` when a core
dependency is down. AI provider failures remain visible as `degraded` capability
entries and do not make the core API unready.

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
      { "name": "malware_scanner", "state": "ok", "detail": null, "latency_ms": 1.2, "cached": false },
      { "name": "llm_provider", "state": "degraded", "detail": "ProviderError: capability check failed; see structured startup logs.", "cached": true }
    ]
  }
}
```

`GET /health` and `GET /ready` are temporary compatibility aliases and should
not be used by new integrations.

## GET /metrics

Admin-gated Prometheus-compatible current operational gauges. See
[operator_api.md](operator_api.md) for the JSON operator surfaces.
