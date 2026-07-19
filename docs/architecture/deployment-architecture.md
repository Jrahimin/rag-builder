# Deployment Architecture

> **Canonical source** for local development and dedicated hosted production topology.

---

## Deployment modes

| Mode | Purpose | Status |
| ---- | ------- | ------ |
| **Local development** | Developer machine, fast iteration | ✅ Implemented |
| **Dedicated hosted production runtime** | Isolated customer backend, console, webhooks, and recovery contract | ✅ Phase 6 supported pilot profile |
| **Supported self-hosted edition** | Customer-operated release (Future F1) | Deferred / demand-led |

---

## Local development (implemented)

### Full Docker stack

```bash
docker compose up --build
```

```text
┌─────────────────────────────────────────────────────────┐
│  docker-compose (single host)                           │
│                                                         │
│  ┌──────────┐  ┌───────────────────┐  ┌────────┐     │
│  │ backend  │  │ postgres+pgvector │  │ redis  │     │
│  │ :8000    │  │ :5432             │  │ :6379  │     │
│  └──────────┘  └───────────────────┘  └────────┘     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ worker   │  │ minio    │  │migrate /  │             │
│  │ Taskiq   │  │ :9000    │  │minio-init │             │
│  └──────────┘  └──────────┘  └──────────┘             │
└─────────────────────────────────────────────────────────┘
```

- **API process:** single container running uvicorn with `--reload`
- **Durable dispatcher:** lifespan task in each API process; concurrent replicas
  coordinate outbox and expired-lease claims through PostgreSQL row locks.
- **Worker process:** separate Taskiq container; starts after migrations and
  bucket bootstrap, and can be scaled independently outside local development.
- **Volumes:** named volumes for data persistence
- **Networking:** `ape_network` bridge; service DNS names for inter-container comms
- **Bootstrap:** one-shot `migrate` applies `alembic upgrade head`; `minio-init`
  creates the artifact bucket. API and worker start only after both succeed.
- **Images:** PostgreSQL uses `pgvector/pgvector:0.8.1-pg16`; MinIO also uses an
  explicit release tag. Upgrade either deliberately after migration testing.

### Hybrid mode

Infrastructure in Docker, API on host with venv — see `docs/learning/docker-local-development.md`.

---

## Dedicated hosted production runtime

```text
┌──────────────── Business Application ────────────────┐
│                      REST                             │
└────────────────────────┬────────────────────────────┘
                         ▼
┌──────────────── AI Platform Engine ──────────────────┐
│  ┌─────────────┐         ┌─────────────┐             │
│  │ API (×N)    │         │ Worker (×M) │             │
│  │ gunicorn +  │         │ Taskiq worker│             │
│  │ uvicorn     │         │             │             │
│  └──────┬──────┘         └──────┬──────┘             │
│         └──────────┬────────────┘                    │
│                    ▼                                 │
│     PostgreSQL+pgvector │ Redis │ MinIO/S3           │
└──────────────────────────────────────────────────────┘
```

| Component | Scaling | Notes |
| --------- | ------- | ----- |
| API | Horizontal (stateless) | Behind load balancer |
| Worker | Horizontal | Scales with queue depth |
| PostgreSQL + pgvector | Vertical / managed | Single primary (Phase 1); `vector` extension required |
| Redis | Single instance / managed | At-least-once Taskiq transport + cache; PostgreSQL retains dispatch intent and execution state |
| Object storage | S3 / MinIO | Customer-owned |

The supported pilot is deliberately a single-host Compose topology. It exposes only the
TLS gateway; data services stay internal while API/workers/ClamAV have explicit egress
for providers, customer webhooks, and updates. Images are digest-pinned and validated.

---

## Docker organization

| File | Purpose |
| ---- | ------- |
| `docker-compose.yml` | Local development stack (repo root) |
| `backend/Dockerfile` | Multi-stage image: `development` target for reload; default `production` target uses Gunicorn/Uvicorn workers |
| `infra/hosted/compose.yaml` | Supported dedicated hosted pilot topology |
| `infra/hosted/hostedctl.py` | Immutable-release validation and guarded recovery operations |
| `.dockerignore` | Build context exclusions |

`APE_RUNTIME__PROFILE` selects `hosted_openai` or `private_ollama`; validation and
bounded preflight run in API and workers before they serve or consume. Migration is a
one-shot gate. Every service has a probe and resource limit. Release/runtime/TLS inputs
are injected at deployment and never baked into images.

Backups quiesce writes and capture PostgreSQL plus object storage with a release manifest.
Upgrade takes a pre-upgrade snapshot. Rollback restores both data and prior digest-pinned
images; it never assumes Alembic downgrade safety. The canonical procedure is the
[`hosted runbook`](../../infra/hosted/RUNBOOK.md).

---

## Infrastructure ownership

Each dedicated customer deployment contains:

- PostgreSQL with pgvector, Redis, object storage
- AI model endpoints (Ollama, vLLM, cloud APIs)
- All Project data within the deployment

The initial product is operator-managed dedicated hosting with no shared
customer data plane. Customer-operated self-hosting, release packaging, and its
support boundary are Future F1 work.

---

## Health endpoints

| Endpoint | Process | Purpose |
| -------- | ------- | ------- |
| `GET /health` | API | Liveness |
| `GET /ready` | API | Cheap dependency readiness plus cached provider preflight results |
| `GET /metrics` | API | Admin-gated Prometheus-compatible current gauges |
| `GET /api/v1/operator/*` | API | Admin-gated dependencies, workers, metrics, configuration, failures, and audit |

Managed PostgreSQL must support `CREATE EXTENSION vector`. If the application
migration role cannot create extensions, the platform operator provisions it
before `alembic upgrade head`. The configured embedding dimension must match the
deployment's `vector(n)` column; dimension changes require migration plus
re-embedding.

Back up the whole PostgreSQL database, including pgvector rows, with the normal
PostgreSQL backup tooling. Restore into a server with the `vector` extension
available before applying schema migrations. See the
[pgvector operations runbook](../learning/pgvector-operations-runbook.md) for
cutover, verification, HNSW maintenance, and recovery.

Taskiq workers publish expiring Redis heartbeats. A graceful shutdown deletes its heartbeat;
process loss is detected by TTL. The operator workers API reports heartbeat age and active count.

---

## Related

- [Background processing](./background-processing.md)
- `docs/learning/docker-local-development.md`
- ADR-006: Deployment topology
