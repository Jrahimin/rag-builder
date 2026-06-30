# System — health probes

Unversioned infrastructure endpoints (not under `/api/v1`).

## GET /health

Liveness — process is running; does not probe dependencies.

**Response** `200`

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "service": "ape",
    "version": "0.1.0",
    "environment": "development"
  }
}
```

## GET /ready

Readiness — probes PostgreSQL, Redis, Qdrant, MinIO.

**Response** `200` when all dependencies reachable; `503` when degraded.

```json
{
  "success": true,
  "data": {
    "status": "ready",
    "service": "ape",
    "version": "0.1.0",
    "environment": "development",
    "dependencies": [
      { "name": "postgresql", "state": "ok", "detail": null, "latency_ms": 1.2 }
    ]
  }
}
```
