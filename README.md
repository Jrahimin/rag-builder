# AI Platform Engine (APE)

### Drop AI into your product. Keep your stack. Own your data.

> Self-hosted RAG infrastructure for real applications — not another chatbot demo.
> One API. Many products. Your documents, your models, your deployment.

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://docs.docker.com/compose/)
[![Ruff](https://img.shields.io/badge/lint-ruff-D7FF64.svg)](https://docs.astral.sh/ruff/)
[![Phase 1](https://img.shields.io/badge/Phase%201-end--to--end%20RAG-success.svg)](docs/Platform-at-a-glance.md)

**Ship grounded AI without rebuilding the plumbing.**

[Integration Guide](docs/platform-integration-guide.md) ·
[Platform at a Glance](docs/Platform-at-a-glance.md) ·
[API Reference](docs/api/README.md) ·
[Architecture](docs/architecture/README.md) ·
[Features](docs/features/README.md)

---

## Why teams reach for APE

Every product that needs “ask our documents” eventually rebuilds the same stack:
upload, parse, chunk, embed, search, chat, citations, workers, auth, isolation.

**APE is that stack — once — as a microservice beside your app.**

| You keep | APE owns |
| -------- | -------- |
| UI, users, business workflows | Ingestion → indexing → hybrid retrieval → RAG chat |
| Brand and product experience | Provider-agnostic LLMs, embeddings, storage, vectors |
| Customer trust boundary | Self-hosted deployment under *your* control |

```text
Your app  ──REST + API key──►  APE  ──►  PostgreSQL · Qdrant · Redis · Storage · LLMs
```

Phase 1 is a complete journey:

```text
Organization key → Project → Upload → Parse → Chunk → Embed → Index → Search → Chat
```

---

## One platform. Many products.

Same engine. Different corpora. Different experiences.

| Build this… | With APE as… |
| ----------- | ------------ |
| “Ask this folder” in a DMS | Project-scoped knowledge + citations |
| Tax / audit research assistant | Hybrid search over regs + working papers |
| Legal matter Q&A | Isolated projects per matter or client |
| HR policy copilot | Handbook corpus per org / region |
| Internal knowledge for many apps | One reusable AI service, many Projects |
| Vertical SaaS AI add-on | Embedded infrastructure, not a rewrite |

**Project** = which corpus. **Organization** = who is calling.
Wire once; spin up as many product surfaces as you need.

---

## What ships today

| | Capability | You get |
| - | ---------- | ------- |
| 🔐 | **Organizations & API keys** | Tenant auth, admin bootstrap, org-scoped rate limits |
| 📁 | **Projects** | Hard data isolation for documents, search, and chat |
| 📄 | **Knowledge** | Upload → storage → parse → optional OCR → structure-aware chunking |
| 🔎 | **Retrieval** | Embeddings, vector + keyword index, hybrid search, reranking |
| 💬 | **Conversations** | Stateful RAG chat, citation snapshots, SSE streaming |
| ⚙️ | **Operations** | Docker Compose, Alembic, `/health` + `/ready`, structured logs |

Full product tour → [Platform at a Glance](docs/Platform-at-a-glance.md)

---

## Architecture in one glance

```text
Business application
        │  REST + API key
        ▼
api/v1/routes/                 HTTP · validation · Depends
        │
        ▼
modules/<feature>/services/    Orchestration · transactions
        │
        ├── repositories/      PostgreSQL
        └── providers/         LLM · embeddings · vectors · storage · OCR
```

Built to stay replaceable:

- **Project-scoped** data paths — no global corpus leakage
- **Provider interfaces** — swap OpenAI / Ollama / Gemini / Qdrant / MinIO without rewriting business logic
- **Worker-first AI** — parse, embed, and index never block HTTP
- **Versioned APIs** under `/api/v1`

Canonical layout → [module architecture](docs/architecture/module-architecture.md)

---

## Quick start

Commands below work in any Unix-style shell (macOS, Linux, Git Bash / WSL on Windows).
Docker Desktop is the recommended path on Windows.

### Option A — Full stack (closest to production)

```bash
git clone <repository-url> rag-builder
cd rag-builder

cp .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

| Surface | URL |
| ------- | --- |
| API | http://localhost:8000 |
| OpenAPI / Swagger | http://localhost:8000/docs |
| Health / Ready | http://localhost:8000/health · `/ready` |
| Qdrant | http://localhost:6333/dashboard |
| MinIO console | http://localhost:9001 |

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker down          # stop
docker compose --env-file .env.docker down -v       # stop + wipe volumes
```

### Option B — Local API + Docker infra (fast iteration)

```bash
# from repo root — infra only
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up -d postgres redis qdrant minio minio-init

cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
cp .env.example .env
alembic upgrade head
python -m app
```

| Surface | URL |
| ------- | --- |
| API | http://localhost:8088 |
| OpenAPI | http://localhost:8088/docs |

`/health` = process alive. `/ready` = PostgreSQL, Redis, Qdrant, and storage.

---

## First API journey

Six calls from empty deployment to cited answers:

1. Create an **Organization** + API key  
2. Create a **Project**  
3. **Upload** a document  
4. Wait for ingest / index (poll status or worker handoff)  
5. **Search** (hybrid retrieval)  
6. **Chat** with citations  

Copy-paste walkthrough → **[Platform Integration Guide](docs/platform-integration-guide.md)**  
Endpoint samples → [docs/api/](docs/api/README.md) · live contract → `/docs`

---

## Develop

From the repo root unless noted:

```bash
ruff check .
ruff format .
mypy
pytest
pre-commit run --all-files
```

```bash
pytest -m unit
pytest -m integration
pytest -m architecture
```

Migrations (`backend/`):

```bash
cd backend
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

---

## Repository map

```text
backend/app/
  api/            HTTP composition, health, /api/v1 routers
  composition/    ORM registry + Alembic migrations
  core/           config, logging, middleware, exceptions
  dependencies/   FastAPI DI wiring
  models/         shared SQLAlchemy ORM
  modules/        projects · organizations · knowledge · retrieval · conversations
  platform/       db · persistence · providers · jobs · auth · http

docs/
  platform-integration-guide.md   ← start here to integrate
  Platform-at-a-glance.md         ← product + architecture story
  api/  architecture/  features/  learning/
```

---

## Documentation

| Goal | Doc |
| ---- | --- |
| Integrate APE into an app | [Platform Integration Guide](docs/platform-integration-guide.md) |
| Understand the product end-to-end | [Platform at a Glance](docs/Platform-at-a-glance.md) |
| Postman-ready endpoint samples | [API reference](docs/api/README.md) |
| Modules, providers, deployment | [Architecture](docs/architecture/README.md) |
| Feature behavior | [Features](docs/features/README.md) |
| Why / how deep dives | [Learning](docs/learning/README.md) |
| Decision records | [ADRs](docs/architecture/adr/README.md) |

Feature deep-dives:
[Organizations](docs/features/organization_module.md) ·
[Projects](docs/features/project_module.md) ·
[Knowledge](docs/features/knowledge_module.md) ·
[Retrieval](docs/features/retrieval_module.md) ·
[Conversations](docs/features/conversation_module.md)

---

## Roadmap

| Horizon | Focus |
| ------- | ----- |
| **Now** | Phase 1 complete: auth → ingest → hybrid retrieve → cited chat |
| **Next** | Connectors, observability, evaluation, ops hardening |
| **Later** | Enterprise connectors, cost analytics, model registry, prompt library |

---

## Philosophy

APE is **platform-first** and **learning-first**.

Not another single-purpose chatbot. A deployable AI layer that product teams can
reuse across DMS, audit, legal, HR, ERP, and internal tools — inspectable,
provider-agnostic, and owned by the deployment that runs it.

*Your application keeps the experience. APE owns the AI lifecycle.*
