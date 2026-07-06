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

> **Status:** Phase 1 in progress — **Project Management**, **Knowledge** (upload →
> parse → chunk), **Retrieval** (embed → index → semantic search baseline), and
> **Conversations** (RAG chat on semantic baseline, ADR-008) are shipped. See
> [docs/features/](docs/features/).

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
      Infrastructure Providers (platform/providers/implementations/)
      └── Vector Store, Storage, Embeddings, Document Parsers, ...
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
├── backend/                # Self-contained Python API service
│   ├── app/
│   │   ├── composition/    # ORM registry (Alembic model discovery)
│   │   ├── api/            # HTTP composition (health + /api/v1 + routes/)
│   │   ├── dependencies/   # FastAPI DI (composition only)
│   │   ├── core/           # Config, logging, middleware, exceptions
│   │   ├── platform/       # Shared kernel (db, infra, persistence, jobs)
│   │   ├── modules/        # Feature vertical slices (bounded contexts)
│   │   └── main.py         # Application factory + ASGI entrypoint
│   ├── requirements/       # Pinned dependencies (base, dev, prod)
│   ├── alembic.ini
│   ├── .env.example        # Local venv config template (copy to .env)
│   ├── Dockerfile
│   └── venv/               # Local virtual environment (gitignored)
├── .env.docker.example     # Docker / full-stack config template (copy to .env.docker)
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

## Installation

```bash
# 1. Clone
git clone <repository-url> rag-builder
cd rag-builder/backend

# 2. Create & activate a virtual environment
python -m venv venv
# macOS / Linux:
source venv/bin/activate
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1

# 3. Install development dependencies
pip install -r requirements/dev.txt

# 4. Create your local env file (from backend/)
cp .env.example .env          # macOS/Linux
# Copy-Item .env.example .env  # Windows PowerShell
```

## Local Development

Run the API from `backend/` after migrations. Two equivalent ways to start
uvicorn — pick whichever fits your workflow:

```bash
cd backend
alembic upgrade head

# Option A — shortcut; reads APE_SERVER__HOST, APE_SERVER__PORT, APE_SERVER__RELOAD
#            from backend/.env (default port 8088 in .env.example)
python -m app

# Option B — explicit uvicorn; host and port on the command line
uvicorn app.main:app --reload --host 0.0.0.0 --port 8088 --reload-dir app
```

**Without Docker:** use your own local PostgreSQL (and optionally Redis/Qdrant).
The API starts even when Redis/Qdrant are down; `/ready` reports degraded until
they are available.

**With Docker for infrastructure only** (API still in venv), from repo root:

```bash
cp .env.docker.example .env.docker   # first time only
docker compose --env-file .env.docker up -d postgres redis qdrant minio
```

**Full stack in Docker** (from repo root):

```bash
cp .env.docker.example .env.docker   # first time only
docker compose --env-file .env.docker up --build -d
```

## Docker Setup

The entire stack — **FastAPI + PostgreSQL + Redis + Qdrant + MinIO** — starts
with a single command. Migrations run automatically before the API boots.

```bash
cp .env.docker.example .env.docker   # first time only
docker compose --env-file .env.docker up --build
```

| Service        | URL / Endpoint (host port from `.env.docker`; default 8088) |
| -------------- | ----------------------------------------------------------- |
| API            | http://localhost:8088                                       |
| API docs       | http://localhost:8088/docs                                  |
| Liveness       | http://localhost:8088/health                                |
| Readiness      | http://localhost:8088/ready                                 |
| Qdrant         | http://localhost:6333/dashboard         |
| MinIO console  | http://localhost:9001                   |

Useful commands:

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker down
docker compose --env-file .env.docker down -v
```

## Running the Application

**Local venv** (default port `8088` in `backend/.env` / `.env.example`):

- **API base:** `http://localhost:8088`
- **Interactive docs (Swagger):** `http://localhost:8088/docs`
- **OpenAPI schema:** `/openapi.json`

**Docker stack** — host port comes from `BACKEND_PORT` in `.env.docker`
(default `8088` in `.env.docker.example`; container listens on 8000 internally).

Health probes (adjust port if you overrode it):

```bash
curl http://localhost:8088/health   # liveness (always 200 while running)
curl http://localhost:8088/ready    # readiness (200 healthy / 503 degraded)
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

Run from the **repo root** (with `backend/venv` activated or tools on your PATH):

```bash
ruff check .
ruff format .
ruff check . --fix
mypy
pytest
pre-commit install          # one-time: install git hooks
pre-commit run --all-files
```

Create a new database migration (from `backend/`):

```bash
cd backend
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

Conventions:

- **Explicit, domain-specific filenames** (e.g. `health_service.py`, not `service.py`).
- **No SDK leakage** — vendor SDKs stay inside `providers/`.
- **Configuration-driven** — nothing AI- or infra-related is hardcoded.
- **Project-scoped by default** — all data access is scoped by `project_id`.
- Create database changes via Alembic migrations (see above).

## Documentation Structure

| Path | Contents |
| ---- | -------- |
| `docs/architecture/` | How the platform is built — start with [system-architecture.md](docs/architecture/system-architecture.md) |
| `docs/learning/` | Foundation deep-dives (config, logging, DB, Docker, testing, FastAPI factory) |
| `docs/features/` | Per-feature reference ([projects](docs/features/project_module.md), [knowledge](docs/features/knowledge_module.md), [retrieval](docs/features/retrieval_module.md), [conversations](docs/features/conversation_module.md)) |
| `docs/api/` | Postman-oriented API reference |

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
