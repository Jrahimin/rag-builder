# Deployment Architecture

> Canonical topology for local full-stack use and ordinary single-VPS
> production.

## Deployment modes

| Mode | Entry point | Purpose |
| --- | --- | --- |
| Local full stack | Root `docker-compose.yml` | Production-like verification on a developer machine |
| Single-VPS production | Root `docker-compose.yml` | Cloudflare and host-Nginx deployment |
| Host-side development | Root Compose infrastructure plus host processes | Fast Python/Vite reload |
| Specialized hosted pilot | `infra/hosted/compose.yaml` | Digest-pinned operator-managed release and recovery contract |

The hosted-pilot profile is intentionally separate. It is not an override for
the ordinary root stack.

## Canonical root stack

```bash
cp .env.example .env
docker compose up -d --build
```

`.env` is ignored runtime configuration and the only secret-bearing Compose
environment file. `.env.example` is the tracked, sanitized template. No
`--env-file` flag or Compose override is required.

```text
Cloudflare → host Nginx :443 → 127.0.0.1:3010 frontend
                                  └→ 127.0.0.1:8010 API (Uvicorn ×2)
                                                     ├→ PostgreSQL + pgvector
                                                     ├→ Redis
                                                     ├→ S3-compatible storage (MinIO)
                                                     └→ ClamAV
                                      Taskiq worker ─┘
```

Every published Docker port is bound to loopback. PostgreSQL `5433`, Redis
`6380`, and MinIO `9010`/`9011` are available for local debugging or SSH
tunnels without being public. ClamAV remains Docker-network-only.

## Process and readiness model

- The production backend image starts direct Uvicorn with two workers. Its
  Dockerfile `CMD` is the only production worker-count setting.
- Each API process runs lifespan-managed durable job and webhook dispatchers.
  PostgreSQL row locks and leases coordinate concurrent claims.
- A separate Taskiq worker performs parsing, OCR, chunking, embedding, and
  indexing. It publishes an expiring Redis heartbeat.
- ClamAV scans uploads synchronously in the API before storage/job handoff, so
  it is mandatory for the API but is not a worker dependency.
- `migrate` waits only for PostgreSQL and applies `alembic upgrade head` once.
- `minio-init` waits only for MinIO and idempotently creates the artifact
  bucket.
- API and worker wait for both successful one-shot gates and their live
  PostgreSQL, Redis, and MinIO dependencies. Only the API also waits for
  ClamAV.
- Frontend can serve independently and show an API-unavailable state.

Long-running services use `restart: unless-stopped`. PostgreSQL, Redis, MinIO,
and ClamAV signature data use named volumes. Docker-local logging is limited to
three 10 MiB files per service.

## pgvector dimension contract

`APE_EMBEDDING__DIMENSIONS=384` is read by migration `0015`, which creates a
`vector(384)` column. Core readiness verifies:

1. the database is at the checked-in migration head;
2. pgvector is installed;
3. the column is `vector(384)`; and
4. the configured malware scanner is reachable.

Embedding-provider dimension validation runs as an advisory capability check.
It is reported as degraded without taking down core authentication, project, and
operator APIs; embedding-dependent operations remain unavailable until fixed. A
future dimension change still requires a schema migration and full re-embedding;
it is not an environment-only change.

## Public edge

Cloudflare uses Full (strict) TLS to host Nginx. Nginx routes `/api/`,
`/health/`, `/metrics`, `/docs`, and `/openapi.json` to the backend before the
`/` frontend fallback. The frontend container serves static files only. Host
Nginx refreshes Cloudflare `real_ip` ranges, replaces forwarded headers, disables
buffering for API/SSE traffic, and applies the upload and long-generation
timeouts. `/docs` remains intentionally disabled in production; `/openapi.json`
is the live integration contract. Uvicorn accepts forwarded headers only because
the backend is loopback-only and host Nginx overwrites them; exposing `8010`
publicly would invalidate that trust boundary.

See [`infra/vps/README.md`](../../infra/vps/README.md) and the checked-in
[Nginx example](../../infra/vps/nginx/rag-builder.conf).

## Health and operations

| Endpoint | Purpose |
| --- | --- |
| `GET /health/live` | API process liveness |
| `GET /health/ready` | Database/pgvector, Redis, S3-compatible storage, ClamAV, and cached AI capability status |
| `GET /metrics` | Admin-gated current metrics |
| `GET /api/v1/operator/workers` | Active Taskiq heartbeat status |

Back up PostgreSQL and MinIO together before upgrades. Restore matching data and
images for rollback instead of assuming an Alembic downgrade is safe. See the
[pgvector operations runbook](../learning/pgvector-operations-runbook.md).

## Deployment files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Canonical local/full-stack and ordinary VPS topology |
| `.env` | Ignored runtime configuration and secrets |
| `.env.example` | Sanitized runtime template |
| `backend/Dockerfile` | Shared production API/worker/migration image |
| `frontend/Dockerfile` | Built operator console served by Nginx |
| `infra/vps/` | Ordinary VPS runbook and host-Nginx example |
| `infra/hosted/` | Separate specialized hosted-pilot contract |

## Related

- [Background processing](./background-processing.md)
- [Docker local development](../learning/docker-local-development.md)
- [ADR-006: API and Worker Process Separation](./adr/006-deployment-topology.md)
