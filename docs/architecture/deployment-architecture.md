# Deployment Architecture

> **Canonical source** for local development vs self-hosted production topology.

---

## Deployment modes

| Mode | Purpose | Status |
| ---- | ------- | ------ |
| **Local development** | Developer machine, fast iteration | ✅ Implemented |
| **Self-hosted production** | Customer-owned infrastructure | ⏳ Documented, partial implementation |

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
│  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌─────────┐ │
│  │ backend  │  │ postgres │  │ redis  │  │ qdrant  │ │
│  │ :8000    │  │ :5432    │  │ :6379  │  │ :6333   │ │
│  └──────────┘  └──────────┘  └────────┘  └─────────┘ │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ worker   │  │ minio    │  │migrate /  │             │
│  │ Taskiq   │  │ :9000    │  │minio-init │             │
│  └──────────┘  └──────────┘  └──────────┘             │
└─────────────────────────────────────────────────────────┘
```

- **API process:** single container running uvicorn with `--reload`
- **Worker process:** separate Taskiq container; starts after migrations and
  bucket bootstrap, and can be scaled independently outside local development.
- **Volumes:** named volumes for data persistence
- **Networking:** `ape_network` bridge; service DNS names for inter-container comms
- **Bootstrap:** one-shot `migrate` applies `alembic upgrade head`; `minio-init`
  creates the artifact bucket. API and worker start only after both succeed.
- **Images:** Qdrant and MinIO use explicit release tags; upgrade them deliberately
  after testing rather than inheriting a moving `latest` tag.

### Hybrid mode

Infrastructure in Docker, API on host with venv — see `docs/learning/docker-local-development.md`.

---

## Self-hosted production (planned)

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
│     PostgreSQL │ Redis │ Qdrant │ MinIO/S3          │
└──────────────────────────────────────────────────────┘
```

| Component | Scaling | Notes |
| --------- | ------- | ----- |
| API | Horizontal (stateless) | Behind load balancer |
| Worker | Horizontal | Scales with queue depth |
| PostgreSQL | Vertical / managed | Single primary (Phase 1) |
| Redis | Single instance / managed | Queue + cache |
| Qdrant | Single / clustered | Per deployment |
| Object storage | S3 / MinIO | Customer-owned |

---

## Docker organization

| File | Purpose |
| ---- | ------- |
| `docker-compose.yml` | Local development stack (repo root) |
| `backend/Dockerfile` | Multi-stage image: `development` target for reload; default `production` target uses Gunicorn/Uvicorn workers |
| `infra/` | Future: production compose overrides, K8s manifests |
| `.dockerignore` | Build context exclusions |

Production will add `docker-compose.prod.yml` or `infra/production/` — not yet present.

---

## Infrastructure ownership

Each customer deployment owns:

- PostgreSQL, Redis, Qdrant, object storage
- AI model endpoints (Ollama, vLLM, cloud APIs)
- All Project data within the deployment

No shared multi-tenant cloud platform.

---

## Health endpoints

| Endpoint | Process | Purpose |
| -------- | ------- | ------- |
| `GET /health` | API | Liveness |
| `GET /ready` | API | Readiness (DB, Redis, Qdrant, MinIO) |

Worker health check (future): separate lightweight probe on worker process.

---

## Related

- [Background processing](./background-processing.md)
- `docs/learning/docker-local-development.md`
- ADR-006: Deployment topology
