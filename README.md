# AI Platform Engine (APE)

> A self-hosted, provider-agnostic RAG platform for enterprise applications.
> Bring your documents, keep your infrastructure, expose the AI layer through
> clean Project-scoped REST APIs.

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688.svg)](https://fastapi.tiangolo.com/)
[![Ruff](https://img.shields.io/badge/lint-ruff-D7FF64.svg)](https://docs.astral.sh/ruff/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://docs.docker.com/compose/)
[![License](https://img.shields.io/badge/license-Proprietary-lightgrey.svg)](#)

**Start here:** [Platform Integration Guide](docs/platform-integration-guide.md) ·
[Platform at a Glance](docs/Platform-at-a-glance.md) ·
[API reference](docs/api/README.md) ·
[Architecture](docs/architecture/README.md) ·
[Features](docs/features/README.md)

---

## What Is APE?

AI Platform Engine is a modular AI infrastructure service that sits beside a
business application. The application keeps the product experience; APE handles
the RAG lifecycle behind stable APIs:

```text
Organization key -> Project -> Upload -> Parse -> Chunk -> Embed -> Index -> Search -> Chat
```

APE is built for private deployments and enterprise integration. It is useful
when you want document intelligence, search, chat, citations, and future AI
workflows without binding your product to a single AI vendor or hosted platform.

Phase 1 now includes the full end-to-end core journey: Organizations and API
keys, Projects, Knowledge ingestion, hybrid Retrieval, and Conversations with
grounded answers.

---

## What Ships Today

| Area | What it provides |
| ---- | ---------------- |
| Organizations and API keys | Tenant boundary, admin bootstrap, M2M auth, org-scoped rate limiting |
| Projects | Data isolation boundary for documents, search, embeddings, and chat |
| Knowledge | Upload, storage, parsing, optional OCR, structure-aware chunking |
| Retrieval | Embeddings, Qdrant indexing, keyword indexing, hybrid search, reranking |
| Conversations | Stateful RAG chat, citation snapshots, SSE streaming |
| Operations | Docker Compose, Alembic migrations, health/readiness probes, structured logs |

Read the richer product tour in
[docs/Platform-at-a-glance.md](docs/Platform-at-a-glance.md).

---

## Architecture In One Screen

```text
Business application
        |
        | REST + API key
        v
api/v1/routes/              HTTP validation, response models, Depends
        |
        v
modules/<feature>/services/ Business orchestration and transactions
        |
        +--> repositories/   PostgreSQL persistence
        |
        +--> providers/      LLM, embeddings, vector store, storage, OCR
```

APE uses a modular architecture with strict boundaries:

- `Project` is the data isolation boundary.
- `Organization` is the auth and tenant boundary.
- Vendor SDKs stay behind provider interfaces.
- Long-running AI work runs through background workers.
- API routes are versioned under `/api/v1`.

Canonical details live in
[docs/architecture/module-architecture.md](docs/architecture/module-architecture.md).

---

## Quick Start: Full Docker Stack

Use this when you want the closest "live stack" experience on one machine.
Docker starts the API, worker, PostgreSQL, Redis, Qdrant, MinIO, and the MinIO
bucket bootstrap.

```bash
git clone <repository-url> rag-builder
cd rag-builder

cp .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

Windows PowerShell:

```powershell
git clone <repository-url> rag-builder
cd rag-builder

Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

Default Docker endpoints:

| Service | URL |
| ------- | --- |
| API | `http://localhost:8000` |
| Swagger / OpenAPI | `http://localhost:8000/docs` |
| Health | `http://localhost:8000/health` |
| Readiness | `http://localhost:8000/ready` |
| Qdrant dashboard | `http://localhost:6333/dashboard` |
| MinIO console | `http://localhost:9001` |

Useful Docker commands:

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker down
```

To remove local volumes too:

```bash
docker compose --env-file .env.docker down -v
```

---

## Quick Start: Local Backend

Use this when you want fast backend iteration in a Python virtual environment.
You can either run PostgreSQL, Redis, Qdrant, and MinIO yourself, or start only
the infrastructure services with Docker.

Start infrastructure from the repo root:

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up -d postgres redis qdrant minio minio-init
```

Then run the backend locally:

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env
alembic upgrade head
python -m app
```

Windows PowerShell:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements/dev.txt
Copy-Item .env.example .env
alembic upgrade head
python -m app
```

Default local backend endpoints:

| Surface | URL |
| ------- | --- |
| API | `http://localhost:8088` |
| Swagger / OpenAPI | `http://localhost:8088/docs` |
| Health | `http://localhost:8088/health` |
| Readiness | `http://localhost:8088/ready` |

`/health` tells you the process is alive. `/ready` reports dependency status for
PostgreSQL, Redis, Qdrant, and storage.

---

## First API Journey

The complete integration path is in the
**[Platform Integration Guide](docs/platform-integration-guide.md)** — auth, projects,
upload, polling, search, and chat with copy-paste examples.

Summary:

1. Create an Organization and API key.
2. Create a Project.
3. Upload a document.
4. Wait for ingestion and indexing.
5. Search with hybrid retrieval.
6. Chat with citations.

OpenAPI is available at `/docs`. The concise Postman-oriented references live in
[docs/api/](docs/api/README.md).

---

## Development Commands

Run from the repo root unless noted.

```bash
ruff check .
ruff format .
mypy
pytest
pre-commit run --all-files
```

Common focused test runs:

```bash
pytest -m unit
pytest -m integration
pytest -m architecture
```

Create and apply a migration from `backend/`:

```bash
cd backend
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

---

## Project Layout

```text
backend/app/
  api/            HTTP composition, health routes, /api/v1 routers
  composition/    ORM registry and Alembic migrations
  core/           config, logging, middleware, exceptions
  dependencies/   FastAPI dependency wiring
  models/         shared SQLAlchemy ORM models
  modules/        feature slices: projects, organizations, knowledge, retrieval, conversations
  platform/       shared kernel: db, persistence, providers, jobs, auth, http

docs/
  platform-integration-guide.md
  Platform-at-a-glance.md
  api/
  architecture/
  features/
  learning/
```

---

## Documentation Map

| If you want... | Read... |
| -------------- | ------- |
| **Integrate APE into your application** | **[Platform Integration Guide](docs/platform-integration-guide.md)** |
| A guided product and architecture overview | [Platform at a Glance](docs/Platform-at-a-glance.md) |
| Endpoint examples for Postman | [API reference](docs/api/README.md) |
| How modules, dependencies, and providers fit together | [Architecture docs](docs/architecture/README.md) |
| Feature-by-feature behavior | [Feature docs](docs/features/README.md) |
| Deep learning journeys and implementation notes | [Learning docs](docs/learning/README.md) |
| Architecture decisions and trade-offs | [ADRs](docs/architecture/adr/README.md) |

Recommended feature reads:
[Organizations](docs/features/organization_module.md),
[Projects](docs/features/project_module.md),
[Knowledge](docs/features/knowledge_module.md),
[Retrieval](docs/features/retrieval_module.md),
[Conversations](docs/features/conversation_module.md).

---

## Roadmap

APE is moving from a complete Phase 1 RAG journey toward a broader enterprise AI
platform.

| Horizon | Direction |
| ------- | --------- |
| Phase 1 hardening | Connector framework, observability, evaluation, better operations |
| Phase 2 enterprise | SQL/API/website/SharePoint/Drive/S3 connectors, cost analytics, model registry, prompt library |

---

## Philosophy

APE is built learning-first and platform-first.

The goal is not to build another single-purpose chatbot. The goal is to build a
deployable AI platform that product teams can reuse, inspect, operate, and adapt
as their providers, models, documents, and business needs change.
