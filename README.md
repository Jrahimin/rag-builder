# AI Platform Engine (APE)

> A deployable, provider-agnostic AI infrastructure platform exposed as
> Project-scoped REST APIs.

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688.svg)](https://fastapi.tiangolo.com/)
[![Ruff](https://img.shields.io/badge/lint-ruff-D7FF64.svg)](https://docs.astral.sh/ruff/)
[![License](https://img.shields.io/badge/license-Proprietary-lightgrey.svg)](#)

---

## Overview

AI Platform Engine (APE) is a production-grade, modular AI platform designed to
integrate with enterprise applications as an independent, self-hosted
microservice. Instead of baking AI capabilities into each business
application, APE provides reusable AI infrastructure through well-defined APIs.

Each customer deploys their own APE instance alongside their application with
complete ownership of their infrastructure, data, models, and configuration. It
is **domain-agnostic** — there is no business-specific logic — so it can be
reused across DMS, tax/audit, ERP, HRMS, legal, financial research, and
internal knowledge-base systems.

> **Status:** Phase 1 in progress — platform foundation plus **Project Management**
> module. See [docs/features/projects.md](docs/features/projects.md).

## Vision

Build a deployable, provider-agnostic AI platform that lets enterprise
applications add modern AI capabilities without implementing AI infrastructure
themselves. Applications talk only to APE's APIs while the platform manages the
full AI lifecycle internally — ingestion, storage, parsing, chunking,
embeddings, retrieval, chat, evaluation, and observability.

Guiding principles: **reusability, provider independence, deployability, data
isolation, production readiness, observability, security, and cost
transparency.**

## Architecture Summary

APE follows a strict, layered architecture with clear separation of concerns:

```text
                Client
                  │
                  ▼
     Router Layer (api/v1/routes/)     HTTP validation, serialization, DI
                  │
                  ▼
     Service Layer (modules/.../services/)   orchestration, transactions
             │            │
             │            ▼
             │     Repository Layer (ProjectScopedRepository)
             │
             ▼
      Infrastructure Providers (future implementations/)
      └── Vector Store, Storage, LLM, OCR, Connectors, ...
```

Key foundations:

- **Composition root** — `api/`, `dependencies/`, `composition/` wire HTTP and ORM discovery; modules do not import `dependencies/`.
- **Application factory** (`create_app`) + ASGI entrypoint (`app.main:app`).
- **API versioning** under `/api/v1` (system probes stay unversioned).
- **Environment-driven configuration** via Pydantic Settings.
- **Structured logging** with per-request `request_id` / `trace_id`.
- **Lifespan-managed infrastructure** (PostgreSQL, Redis, Qdrant connectivity adapters).
- **Global exception handling** with standard error envelope.
- **Async SQLAlchemy 2.x + Alembic** with composition-level ORM registry.
- **Health & readiness** endpoints.
- **Import boundary tests** enforcing documented dependency rules.

Core principle: **Project is the unit of isolation.** `ProjectScopedRepository`
requires `project_id` on every query (business entities ship with the Projects module).

## Technology Stack

| Component        | Technology                                   |
| ---------------- | -------------------------------------------- |
| Language         | Python 3.12                                  |
| Web framework    | FastAPI (async) + Uvicorn                    |
| Validation       | Pydantic v2 / Pydantic Settings              |
| Database         | PostgreSQL + SQLAlchemy 2.x (async, asyncpg) |
| Migrations       | Alembic (async)                              |
| Vector DB        | Qdrant                                       |
| Cache / queue    | Redis                                        |
| Object storage   | MinIO (S3-compatible)                        |
| Logging          | structlog (JSON or console)                  |
| Tooling          | Ruff, Pytest, Mypy, pre-commit               |
| Containerization | Docker + Docker Compose                      |

## Folder Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── composition/    # ORM registry (Alembic model discovery)
│   │   ├── api/            # HTTP composition (health + /api/v1 + routes/)
│   │   ├── dependencies/   # FastAPI DI (composition only)
│   │   ├── core/           # Config, logging, middleware, exceptions
│   │   ├── platform/       # Shared kernel (db, infra, persistence, jobs)
│   │   ├── modules/        # Feature vertical slices (bounded contexts)
│   │   └── main.py         # Application factory + ASGI entrypoint
│   └── Dockerfile
├── tests/
│   └── architecture/       # Import boundary enforcement
├── docs/
│   ├── architecture/       # Canonical architecture guides + ADRs
│   ├── features/           # Per-feature reference docs
│   └── learning/           # Foundation deep-dives
```

## Prerequisites

- **Python 3.12+**
- **Docker** & **Docker Compose v2** (for the full local stack)
- **Git**
- Optional: **make** (developer task runner; otherwise run commands directly)

## Installation

```bash
# 1. Clone
git clone <repository-url> rag-builder
cd rag-builder

# 2. Create & activate a virtual environment
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# 3. Install development dependencies
pip install -r requirements/dev.txt

# 4. Create your local env file
cp .env.example .env          # macOS/Linux
# Copy-Item .env.example .env  # Windows PowerShell
```

## Local Development

Run the supporting services with Docker and the API locally for the fastest
edit/reload loop:

```bash
# Start only the infrastructure (db, redis, qdrant, minio)
docker compose up -d postgres redis qdrant minio

# Apply migrations
alembic upgrade head

# Run the API with autoreload (from the repo root)
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or use the Make targets:

```bash
make up        # start the full stack in Docker
make migrate   # alembic upgrade head
make run       # run the API locally with reload
```

## Docker Setup

The entire stack — **FastAPI + PostgreSQL + Redis + Qdrant + MinIO** — starts
with a single command. Migrations run automatically before the API boots.

```bash
docker compose up --build
```

| Service        | URL / Endpoint                          |
| -------------- | --------------------------------------- |
| API            | http://localhost:8000                   |
| API docs       | http://localhost:8000/docs              |
| Liveness       | http://localhost:8000/health            |
| Readiness      | http://localhost:8000/ready             |
| Qdrant         | http://localhost:6333/dashboard         |
| MinIO console  | http://localhost:9001                   |

Useful commands:

```bash
docker compose ps           # service status & health
docker compose logs -f backend
docker compose down         # stop
docker compose down -v      # stop and remove named volumes
```

## Running the Application

- **API base:** `http://localhost:8000`
- **Interactive docs (Swagger):** `/docs` (disabled in production)
- **OpenAPI schema:** `/openapi.json`

Health probes:

```bash
curl http://localhost:8000/health   # liveness (always 200 while running)
curl http://localhost:8000/ready    # readiness (200 healthy / 503 degraded)
```

Every response carries `X-Request-ID` and `X-Trace-ID` headers for end-to-end
tracing; inbound values are honored if provided.

## Running Tests

```bash
pytest                       # run the suite
pytest --cov --cov-report=term-missing   # with coverage
pytest -m unit               # only unit tests
pytest -m integration        # only integration tests
pytest -m architecture         # import boundary tests
```

Tests run without any external services — the readiness checks report
dependencies as "down" instead of failing. Bring the stack up to see them pass
as healthy.

## Development Workflow

```bash
make lint        # ruff check .
make format      # ruff format + autofix
make typecheck   # mypy
make test        # pytest
make check       # lint + typecheck + test
make hooks       # install pre-commit git hooks
```

Conventions:

- **Explicit, domain-specific filenames** (e.g. `health_service.py`, not `service.py`).
- **No SDK leakage** — vendor SDKs stay inside `providers/`.
- **Configuration-driven** — nothing AI- or infra-related is hardcoded.
- **Project-scoped by default** — all data access is scoped by `project_id`.
- Create database changes via Alembic migrations (`make migration m="..."`).

## Documentation Structure

| Path | Contents |
| ---- | -------- |
| `docs/architecture/` | How the platform is built — start with [system-architecture.md](docs/architecture/system-architecture.md) |
| `docs/learning/` | Foundation deep-dives (config, logging, DB, Docker, testing, FastAPI factory) |
| `docs/features/` | Per-feature reference documentation (populated as modules ship) |

The binding rules live in `.cursor/rules/` (`project-context.mdc` for vision,
scope, and current status; `architecture.mdc` for engineering standards).

## Future Roadmap

**Phase 1 — Core AI Platform**
Project management, connector framework + file upload, object storage, OCR &
parsing, chunking, embeddings, vector storage, hybrid retrieval, chat,
configuration, background jobs, authentication, evaluation & observability
foundations.

**Phase 2 — Enterprise AI Platform**
SQL / API / Website / SharePoint / Google Drive / S3 connectors, voice,
streaming, multi-modal processing, model registry, prompt library, usage &
cost analytics, evaluation dashboard.

**Phase 3 — Advanced AI Platform**
GraphRAG, knowledge graphs, entity extraction, query planning & rewriting,
multi-hop retrieval, memory layer, agent workflows, MCP integration,
benchmarking, distributed inference, Kubernetes deployment.

---

*Built learning-first: understanding the engineering principles is as important
as implementing the features.*
