# Foundation Sprint Overview

This document summarizes **what the foundation sprint built** and why each
piece exists. It is the starting point for understanding the codebase before
implementing Phase 1 features.

---

## Why a foundation sprint?

APE is an enterprise AI platform, not a single RAG script. Before adding
Projects, Documents, or retrieval pipelines, the codebase needs:

- predictable layering (Router → Service → Repository/Provider),
- environment-driven configuration (no hardcoded infra),
- consistent API contracts and error handling,
- async database and migration infrastructure,
- structured logging with traceability,
- a reproducible local development environment,
- tooling ready for CI/CD.

The foundation sprint delivers exactly that — **infrastructure without business
logic**.

---

## What was built

### Project scaffolding

| Artifact | Purpose |
| -------- | ------- |
| `pyproject.toml` | Project metadata + Ruff, Pytest, Mypy, Coverage config |
| `requirements/` | Pinned dependencies (`base`, `dev`, `prod`) |
| `.env.example` | Documented environment template |
| `Makefile` | Common developer tasks (`make help`) |
| `.pre-commit-config.yaml` | Git hooks for formatting and linting |

### Backend layers (`backend/app/`)

```text
api/            HTTP routers (health + empty /api/v1)
core/           Config, logging, middleware, exceptions
db/             Engine, sessions, Redis/Qdrant clients, migrations
dependencies/   FastAPI DI wiring
models/         ORM mixins (no business entities yet)
repositories/   BaseRepository generic CRUD
schemas/        ApiResponse, ErrorResponse, health schemas
services/       HealthService (readiness orchestration)
providers/      Empty — ready for vendor abstractions
workflows/      Empty — ready for LangGraph / state machines
main.py         Application factory + lifespan
```

### Infrastructure connectivity

- **PostgreSQL** — async SQLAlchemy 2.x with connection pooling.
- **Redis** — async client for future cache and job queue (Arq).
- **Qdrant** — async client for health checks; vector ops deferred to providers.
- **MinIO** — probed via HTTP in readiness checks; storage provider deferred.

### Operations

- `GET /health` — liveness (cheap, no dependency calls).
- `GET /ready` — parallel probes of all four dependencies; returns 503 when degraded.
- Alembic baseline migration (`0001_initial`) — empty schema, chain established.

### Docker

- Multi-stage `backend/Dockerfile`.
- `docker-compose.yml` — full local stack with health checks, named volumes,
  auto-migration on boot.

### Quality

- Ruff (lint + format), Pytest (unit + integration), Mypy (strict on `app/`),
  pre-commit hooks.

---

## What was intentionally NOT built

Per sprint scope, these are **out of scope** until Phase 1 feature work:

- Project, Document, Connector modules
- OCR, parsing, chunking, embeddings, retrieval, chat
- Authentication / authorization
- Background job workers (Arq)
- Concrete LLM, storage, or vector store providers
- Business ORM entities and tables (beyond mixins)

---

## How the pieces connect

```mermaid
flowchart TB
    subgraph entry [Entry]
        Uvicorn[uvicorn app.main:app]
        Factory[create_app]
    end

    subgraph cross [Cross-cutting]
        Config[Settings / APE_* env]
        Log[structlog]
        MW[RequestContextMiddleware]
        EH[Exception handlers]
    end

    subgraph api [API]
        Health[/health /ready]
        V1[/api/v1 - empty]
    end

    subgraph infra [Infrastructure clients]
        PG[(PostgreSQL)]
        RD[(Redis)]
        QD[(Qdrant)]
        MN[(MinIO)]
    end

    Uvicorn --> Factory
    Factory --> Config
    Factory --> Log
    Factory --> MW
    Factory --> EH
    Factory --> Health
    Factory --> V1
    Factory --> PG
    Factory --> RD
    Factory --> QD
    Health --> PG
    Health --> RD
    Health --> QD
    Health --> MN
```

---

## Reading order for new contributors

1. This document (overview).
2. `docs/architecture/system-architecture.md` (request flow and layering).
3. Topic deep-dives in `docs/learning/`:
   - `application-factory-and-fastapi.md`
   - `configuration-system.md`
   - `structured-logging.md`
   - `database-and-migrations.md`
   - `docker-local-development.md`
   - `testing-strategy.md`
4. `.cursor/rules/architecture.mdc` (binding engineering rules).

---

## Next sprint: Project Management

The first business module introduces the **Project** entity — the central
isolation boundary. Every subsequent feature scopes data and configuration by
`project_id`.
