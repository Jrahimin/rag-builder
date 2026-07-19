# RAG Builder

### A private AI knowledge engine your product can integrate with ease.

RAG Builder is the product-facing name for **APE (AI Platform Engine)**, the
reusable backend underneath this repository.

RAG Builder helps software products add document ingestion, search, grounded
answers, and citations without rebuilding the entire AI backend from scratch.

![RAG Builder architecture and product journey](docs/assets/RAG_Builder_Hero_Image.png)

> **One reusable engine. Many product experiences.**
>
> Your application keeps the UI, users, and workflow. RAG Builder carries the
> document journey: ingest, parse, chunk, embed, index, retrieve, and generate.

[Start the learning journey](docs/learning/rag-from-zero.md) ·
[Integrate the API](docs/platform-integration-guide.md) ·
[See the architecture](docs/Platform-at-a-glance.md)

---

## Why this exists

The moment a product wants to “ask our documents,” it inherits a surprisingly
large system:

```text
upload -> parse/OCR -> chunk -> embed -> index -> retrieve -> answer -> cite
```

That system also needs project boundaries, object storage, background workers,
provider integration, migrations, observability, and safe corpus changes.

RAG Builder turns that journey into a reusable, product-ready foundation.

```text
Your product  ── REST + API key ──►  RAG Builder
                                      ├─ PostgreSQL + pgvector
                                      ├─ background workers
                                      ├─ object storage
                                      └─ LLM / embedding providers
```

## What a product gets

- Project-scoped document upload, processing, and lifecycle management.
- PDF, DOCX, TXT, and Markdown parsing with page and source provenance.
- Optional OCR fallback for scanned or image-heavy PDF pages.
- Structure-aware, multilingual chunking with source offsets and metadata.
- PostgreSQL-native semantic, keyword, and hybrid retrieval.
- Rank fusion, metadata filtering, reranking, and duplicate-result suppression.
- Stateful grounded conversations, SSE streaming, claim-linked citations, and
  evidence-based refusal when the corpus cannot support an answer.
- Versioned evaluation datasets, durable quality runs, regression metrics, and a
  fast provider-free smoke suite for CI.
- Safe corpus changes through durable jobs, immutable index builds, atomic
  activation, and retained rollback points.
- Signed HMAC webhooks with retry/backoff, delivery history, endpoint
  disablement, and replay-safe event IDs.
- Organization API keys, Project isolation, readiness checks, and diagnostic
  tooling for the full operating environment.
- A browser-based **Test Lab** for verifying upload, processing, retrieval,
  citations, refusal behavior, and corpus lifecycle without Postman.
- A dedicated hosted profile with TLS gateway, backup, restore, upgrade,
  rollback, and diagnostics commands.

## Small examples of what becomes possible

| Product | Instant AI capability |
| --- | --- |
| Law firm workspace | Add matter documents, ask case questions, and return source pages. |
| Call-center platform | Search support policies and draft grounded agent replies. |
| Audit/compliance SaaS | Retrieve evidence from policies, reports, and working papers. |
| HR platform | Answer handbook questions for a selected organization or region. |
| Document management system | Add “ask this folder” without replacing the existing UI. |
| Internal operations tool | Turn procedures and playbooks into a searchable assistant. |

The host product owns the experience. RAG Builder owns the knowledge lifecycle.

## The journey inside the engine

```text
1. Ingest       receive a document and preserve the original
2. Parse/OCR    turn bytes or pixels into text with provenance
3. Chunk        split text into useful, citable passages
4. Embed/Index  represent meaning and build safely activatable search structures
5. Retrieve     find and rank evidence with semantic + keyword search
6. Generate     ask the LLM to answer from the evidence
```

The most important idea is simple:

> **The model should write from evidence, not pretend the evidence does not matter.**

## Learn it like a story

The learning docs are written for people who want to understand both the
concepts and the code.

### Start here

[RAG from Zero: Follow One Question Through the Engine](docs/learning/rag-from-zero.md)

You will follow a question such as “What is our refund policy?” through the
complete pipeline, then open the source files behind each stage.

### Continue through the building blocks

1. [Knowledge ingestion](docs/learning/knowledge-ingestion-journey.md)
2. [Parsing and extraction](docs/learning/document-parsing-and-extraction.md)
3. [OCR fundamentals](docs/learning/ocr-fundamentals.md)
4. [Chunking](docs/learning/text-chunking-for-rag.md)
5. [Embeddings](docs/learning/embeddings-fundamentals.md)
6. [Vector storage and pgvector](docs/learning/vector-storage-and-pgvector.md)
7. [Semantic and hybrid retrieval](docs/learning/semantic-search-for-rag.md)
8. [Conversation RAG and prompting](docs/learning/conversation_rag_journey.md)
9. [Configuration and tuning](docs/learning/configuration-system.md)
10. [Docker local development](docs/learning/docker-local-development.md)

## Architecture at a glance

```text
Business application
        │  REST + organization API key
        ▼
FastAPI routes and project access checks
        │
        ▼
Feature services
  ├── knowledge       upload, parse, chunk, lifecycle
  ├── retrieval       embeddings, index builds, activation/rollback, search
  ├── conversations   context, evidence gate, prompts, LLM calls, citations
  └── evaluation      datasets, quality runs, metrics, regressions
        │
        ├── PostgreSQL + pgvector
        ├── Redis + background workers
        ├── local/MinIO object storage
        └── provider contracts for LLM, embeddings, OCR, parsing, storage
```

The host application owns users, business workflows, authorization decisions,
and domain UI. RAG Builder owns knowledge ingestion, retrieval, grounded chat,
citations, and evaluation. `Project` is the primary data-isolation boundary.

The project uses a modular-monolith shape supported by background workers and
infrastructure services, keeping the core easy to inspect, extend, and deploy.

## Quick start with Docker

Docker Desktop is the easiest way to explore the full local journey.

```bash
git clone <repository-url> rag-builder
cd rag-builder

cp .env.docker.example .env.docker
docker compose --env-file .env.docker up -d --build
```

Local surfaces:

| Surface | URL |
| --- | --- |
| Operator console | `http://localhost:3000/operator/` |
| Test Lab | `http://localhost:3000/operator/lab` |
| API / OpenAPI | `http://localhost:8000` / `http://localhost:8000/docs` |
| Liveness | `http://localhost:8000/health/live` |
| Readiness | `http://localhost:8000/health/ready` |
| MinIO console | `http://localhost:9001` |

The local stack includes the React operator console, FastAPI, a Taskiq worker,
PostgreSQL with pgvector, Redis, MinIO, and one-shot migration/bootstrap
services. Use the Test Lab as the browser-based end-to-end verification surface.

### Useful local commands

```bash
# Full local environment
make up
make status
make logs
make restart
make down

# Quality gate
make quality

# Focused checks
make format-check
make lint
make typecheck
make test-unit
make test-integration
make migration-check
make eval-smoke
make frontend-quality
make frontend-build

# Safe environment diagnostic
cd backend
python -m app.cli doctor
```

`make quality` runs the repository's deterministic formatting, linting, type,
migration, test, evaluation-smoke, and frontend checks. The doctor reports
configuration, PostgreSQL, migration, pgvector, Redis, storage, worker/broker,
embedding, and reranker readiness without printing secrets or calling AI
providers.

For host frontend development with Vite fast refresh:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

For service-specific Docker work:

```bash
docker compose --env-file .env.docker up -d postgres redis
docker compose --env-file .env.docker up -d minio minio-init
docker compose --env-file .env.docker up -d backend worker
docker compose --env-file .env.docker up -d --no-deps frontend
```

## First API journey

The product flow is intentionally small:

1. Create an organization and API key.
2. Create a project for a corpus boundary.
3. Upload a document.
4. Follow processing status.
5. Search for evidence.
6. Ask a grounded question.

The copy-paste integration path is in the
[Platform Integration Guide](docs/platform-integration-guide.md). Endpoint
contracts live in the [API reference](docs/api/README.md).

Public errors use one safe, machine-readable envelope and return a request ID
for support correlation:

```json
{
  "error": {
    "code": "document_not_ready",
    "message": "The document has not completed processing.",
    "request_id": "req_123",
    "details": {}
  }
}
```

## Repository map

```text
backend/app/
  api/            HTTP routes, envelopes, health
  cli/            safe operator commands, including doctor
  dependencies/   request wiring and access checks
  models/         shared SQLAlchemy ORM
  modules/        organizations, projects, knowledge, retrieval,
                  conversations, evaluation, jobs, operations, webhooks
  platform/       database, providers, jobs, auth, persistence
  worker/         background task entrypoints

frontend/         React/TypeScript operator console
infra/hosted/     dedicated deployment profile, gateway, control tool, runbook
scripts/          repository diagnostics and local helpers
docs/             architecture, API, features, learning, and operations guides
tests/            unit, integration, architecture, benchmark, and evaluation tests
```

## Documentation guide

| Need | Start here |
| --- | --- |
| Understand the product | [Platform at a Glance](docs/Platform-at-a-glance.md) |
| Integrate an application | [Platform Integration Guide](docs/platform-integration-guide.md) |
| Learn RAG from the beginning | [RAG from Zero](docs/learning/rag-from-zero.md) |
| Follow document processing | [Knowledge Ingestion Journey](docs/learning/knowledge-ingestion-journey.md) |
| Understand search quality | [Hybrid Retrieval Journey](docs/learning/hybrid-retrieval-journey.md) |
| Understand chat grounding | [Conversation RAG Journey](docs/learning/conversation_rag_journey.md) |
| Study architecture | [Architecture](docs/architecture/README.md) |
| Explore behavior | [Features](docs/features/README.md) |
| Operate corpus and index lifecycle | [Safe corpus/index lifecycle](docs/features/safe_corpus_index_lifecycle.md) |
| Integrate lifecycle APIs | [Index lifecycle API](docs/api/index_lifecycle_api.md) |
| Operate pgvector | [pgvector Operations Runbook](docs/learning/pgvector-operations-runbook.md) |
| Run a dedicated deployment | [Hosted runbook](infra/hosted/RUNBOOK.md) |

## License

Source code is licensed under the [MIT License](LICENSE). The RAG Builder name,
logo, and branding remain owned by the project owner; the MIT grant does not
grant trademark or brand-use rights.
