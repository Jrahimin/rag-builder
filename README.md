# RAG Builder

RAG Builder is a technical-preview knowledge engine for products that need
project-scoped document ingestion, hybrid retrieval, grounded answers, citations,
and evaluation without rebuilding the complete RAG backend.

The host application owns users, business workflows, authorization decisions,
and domain UI. RAG Builder owns knowledge ingestion, processing, retrieval,
grounded chat, citations, durable corpus/index lifecycle, and evaluation.

![RAG Builder architecture and product journey](docs/assets/RAG_Builder_Hero_Image.png)

[Integration guide](docs/platform-integration-guide.md) ·
[API reference](docs/api/README.md) ·
[Architecture](docs/architecture/README.md) ·
[Learning path](docs/learning/rag-from-zero.md)

## Project status

The current `0.9.0` line is a technical preview intended for:

- development and learning;
- internal applications with an engineering owner;
- integration into another product;
- dedicated, customer-specific pilots operated with the supplied runbook.

It is not yet a public multi-tenant SaaS, a fully supported enterprise
distribution, a stable `1.0` public API, or a complete enterprise search
platform. Dedicated deployment tooling is pilot-ready, not a general promise of
supported customer-operated self-hosting.

Status meanings: **Supported** is implemented and covered by normal repository
tests; **Preview** is implemented but its operational/API contract may still
change; **Experimental** is useful for development or comparison only;
**Planned** is documented future work; **Not supported** is intentionally outside
the current product.

| Capability | Status | Current boundary |
| --- | --- | --- |
| Project-scoped document upload and lifecycle | Supported | PDF, DOCX, TXT, Markdown; validated and processed by durable jobs |
| PDF/DOCX parsing and parse provenance | Supported | Page-aware PDF fallback and structured DOCX parsing |
| OCR fallback | Preview | Optional PaddleOCR; disabled by default; stock Bengali OCR is not supported |
| Structure-aware multilingual chunking | Supported | Token-based strategies with page/offset/section metadata |
| PostgreSQL/pgvector embeddings and immutable index builds | Supported | Hash provider is development-only; production profiles require real providers |
| Semantic, keyword, and hybrid retrieval | Supported | PostgreSQL-native vector + BM25/FTS + RRF |
| Metadata filters and configurable reranking | Supported | Allow-listed metadata; lexical/embedding reranker implementations |
| Duplicate/adjacent-result control | Preview | Exact-content, per-document, and per-section suppression after ranking |
| Grounded chat, SSE streaming, citations, and refusal | Supported | Evidence gate and claim-linked citations; API remains pre-1.0 |
| Index validation, activation, retained rollback | Supported | Atomic Project index pointers and guarded lifecycle jobs |
| Versioned evaluation datasets and quality runs | Preview | Durable larger runs plus a provider-free CI smoke suite |
| Signed outcome webhooks | Preview | Versioned events, HMAC, retry/backoff, attempts, disablement, replay |
| Organization API keys and Project isolation | Supported | M2M organization auth; Project remains the data boundary |
| React operator console and Test Lab | Preview | Trusted operator surface; no user/login/RBAC subsystem |
| Docker development environment | Supported | Backend, frontend, worker, PostgreSQL/pgvector, Redis, MinIO |
| Dedicated hosted pilot profile | Preview | Digest-pinned images, TLS gateway, backup/restore/upgrade/rollback tooling |
| Customer-operated supported distribution | Planned | Demand-led packaging and support model |
| Public shared multi-tenant SaaS | Not supported | No shared customer data plane or SaaS control plane |
| Connector framework/marketplace, GraphRAG, agents, Kubernetes, billing | Not supported | Outside this repository hardening scope |

## What is implemented

### Ingestion and document processing

Uploads are Project-scoped and pass size, extension/MIME, signature,
corruption/password, and configurable malware checks before durable processing.
Raw files and parsed artifacts use the storage provider abstraction (local or
MinIO). PDF extraction tries native PyMuPDF text, assesses page quality, uses
PDFium for degraded pages, and can invoke OCR only for pages still below the
quality threshold. The highest-quality page result wins. DOCX, text, and
Markdown parsing preserve useful structure and provenance.

OCR is optional and worker-only. PaddleOCR is the current implementation; it is
not installed by the base environment, is disabled by default, and does not ship
a reliable stock Bengali model. Unicode Bengali text layers, TXT, and DOCX are
supported by the normal multilingual processing path.

The chunker uses shared Unicode normalization/tokenization, structural signals,
headings, semantic boundaries, token budgets, overlap, and page/source offsets.
Embedding providers are abstracted; indexed vectors, keyword rows, term stats,
and fingerprints remain in PostgreSQL/pgvector under immutable Project index
builds.

### Retrieval, chat, and evaluation

Search supports semantic, keyword, and hybrid strategies. The hybrid path runs
semantic and keyword candidate generation, reciprocal-rank fusion, optional
reranking, metadata/document filters, result hydration, and conservative
duplicate suppression. Suppression preserves selected rank order, citations,
and metadata and exposes sanitized reason counts in search diagnostics.

Conversations are stateful and Project-scoped. Streaming and non-streaming turns
share the evidence gate, refusal reasons, citation snapshots, and claim-to-source
mappings. Missing or weak evidence produces an explicit insufficient-evidence
outcome rather than an invented answer.

Evaluation datasets and runs are versioned, corpus-fingerprinted artifacts.
Durable runs compare retrieval/reranker profiles and record ranking, filtering,
refusal, grounding, citation, latency, regression, and failure behavior. The
small CI smoke set is deterministic and does not use paid providers.

### Corpus lifecycle, webhooks, and operations

Corpus-changing work writes private immutable index builds. Validation seals the
document manifest and fingerprints; activation or rollback swaps Project index
pointers transactionally. Reprocess, re-embed, reindex, delete, purge, and
storage reconciliation use durable jobs with leases, heartbeat, retry, progress,
and idempotency controls.

Terminal document outcomes can stage signed webhooks in the same transaction as
the job result. Delivery history, bounded response excerpts, exponential
backoff, endpoint disablement, and replay with stable event IDs are implemented.

The operator console exposes health, jobs, Project/document inspection,
configuration, metrics, audit, evidence quality, webhooks, and a browser Test
Lab. It is an internal deployment console, not a domain application or an end-user
authentication system.

## Architecture

RAG Builder is a modular monolith supported by background workers and local
infrastructure services:

```text
Host product (users, workflows, domain authorization and UI)
                         |
                 REST + organization API key
                         v
RAG Builder FastAPI modular monolith
  |-- organizations/projects   authentication and isolation
  |-- knowledge                upload, parse/OCR, chunk, document lifecycle
  |-- retrieval                embed, index builds, search, ranking
  |-- conversations            evidence gate, grounded chat, citations
  |-- evaluation               datasets, quality runs, regressions
  |-- jobs/operations/webhooks durable execution and operator delivery
                         |
  |-- PostgreSQL + pgvector
  |-- Redis + Taskiq workers
  |-- local or MinIO object storage
  `-- provider contracts for LLM, embedding, OCR, reranking, and storage
```

`Project` is the primary data-isolation boundary: documents, chunks, embeddings,
search, conversations, jobs, evaluations, index builds, and webhook operations
are scoped by `project_id`. `Organization` is the M2M authentication boundary
when API-key auth is enabled; it groups Projects but does not replace Project
scoping.

The canonical layer direction is router → service → repository/provider. Services
own orchestration and transactions, repositories own relational persistence,
providers contain vendor SDKs, and the composition/dependency packages wire the
application.

## Repository structure

```text
backend/app/
  api/             HTTP composition, versioned routers, health and metrics
  cli/             operator-safe commands (`doctor`, reindex helper)
  composition/     shared API/worker wiring, ORM registry, Alembic migrations
  core/            configuration, validation, logging, middleware, error envelopes
  dependencies/    FastAPI dependency composition and access checks
  models/          shared SQLAlchemy ORM entities
  modules/         organizations, projects, knowledge, retrieval, conversations,
                   evaluation, jobs, operations, and webhooks
  platform/        shared database, providers, persistence, auth, jobs, and health
  worker/          Taskiq broker and durable job handlers
frontend/          React/TypeScript/Vite operator console
infra/hosted/      dedicated pilot Compose profile, gateway, control tool, runbook
scripts/           repository diagnostics and local helpers
tests/             unit, architecture, integration, benchmark, and evaluation tests
docs/              API, architecture/ADRs, feature, learning, operations, and plans
```

## Development environment

Copy the checked-in example before running Compose:

```bash
cp .env.docker.example .env.docker
```

Build and start the full environment:

```bash
docker compose --env-file .env.docker up -d --build
```

The equivalent repository entry point is `make up`.

| Surface | URL |
| --- | --- |
| Operator console | `http://localhost:3000/operator/` |
| Test Lab | `http://localhost:3000/operator/lab` |
| API / OpenAPI | `http://localhost:8000` / `http://localhost:8000/docs` |
| Liveness | `http://localhost:8000/health/live` |
| Readiness | `http://localhost:8000/health/ready` |
| MinIO console | `http://localhost:9001` |

Liveness only confirms that the API process responds. Readiness checks
PostgreSQL, the Alembic head, pgvector and its configured dimension, Redis/broker,
and required storage/bucket access. It includes cached startup provider results
but never calls an LLM or embedding provider during health polling. Legacy
`/health` and `/ready` aliases remain temporarily for compatibility.

### Lifecycle and logs

```bash
make status
make logs
make restart
make down
```

The corresponding direct Compose commands are:

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f --tail=200
docker compose --env-file .env.docker restart
docker compose --env-file .env.docker down
```

After Python or Node dependency changes, rebuild the affected images:

```bash
docker compose --env-file .env.docker build --no-cache backend worker frontend
docker compose --env-file .env.docker up -d
```

### Start services separately

These are real services from `docker-compose.yml`:

```bash
docker compose --env-file .env.docker up -d postgres
docker compose --env-file .env.docker up -d redis
docker compose --env-file .env.docker up -d minio minio-init
docker compose --env-file .env.docker up -d backend
docker compose --env-file .env.docker up -d worker
docker compose --env-file .env.docker up -d --no-deps frontend
```

Focused Make targets are `up-db`, `up-redis`, `up-storage`, `up-backend`,
`up-worker`, and `up-frontend`. Compose starts declared dependencies for backend
and worker automatically.

For host-side frontend development:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

### Migrations

Apply migrations through the one-shot Compose service:

```bash
make migrate
```

Create a reviewed migration while the backend and PostgreSQL are running:

```bash
make migration-new name="describe the schema change"
```

Host-side equivalents, after installing backend development requirements, are:

```bash
cd backend
python -m alembic upgrade head
python -m alembic revision --autogenerate -m "describe the schema change"
```

`make migration-check` validates a single reachable migration head without a
database. `make migration-drift-check` compares migrated schema and ORM metadata
against a live configured database.

### Quality and tests

Install backend development dependencies and frontend packages first, then run:

```bash
make quality
```

The unified gate verifies Python formatting, lint, mypy, migration graph, unit
and architecture tests, integration tests, deterministic evaluation smoke cases,
and frontend formatting, lint, type checks, and tests. Database-backed integration
tests run when the explicitly disposable PostgreSQL test database is available;
otherwise they report skips rather than touching an unknown database.

Focused commands:

```bash
make format
make format-check
make lint
make typecheck
make test-unit
make test-integration
make migration-check
make eval-smoke
make frontend-quality
make frontend-build
```

Run the safe diagnostic from the backend environment:

```bash
cd backend
python -m app.cli doctor
```

The doctor validates configuration and reports PostgreSQL, migration head,
pgvector/dimension, Redis, object storage/bucket, broker/worker configuration,
and embedding/reranker selections. It hides secret values, does not call AI
providers, and returns non-zero for critical failures.

Check a running application directly:

```bash
curl --fail --silent http://localhost:8000/health/live
curl --fail --silent http://localhost:8000/health/ready
```

## Dedicated pilot deployment commands

These commands use `infra/hosted/compose.yaml`; they are separate from local
development. Read [the hosted runbook](infra/hosted/RUNBOOK.md) before operating
a deployment. Copy `release.env.example` to `release.env`, copy
`secrets/runtime.env.example` to `secrets/runtime.env`, replace every image
digest/host/secret placeholder, and install TLS files.

From `infra/hosted/`:

```bash
python hostedctl.py validate
docker compose --env-file release.env -f compose.yaml pull
docker compose --env-file release.env -f compose.yaml up -d
docker compose --env-file release.env -f compose.yaml ps
docker compose --env-file release.env -f compose.yaml logs --tail=200 backend worker gateway
```

Guarded operational commands:

```bash
python hostedctl.py backup
python hostedctl.py restore backups/<stamp> --confirm RESTORE:<deployment-id>
python hostedctl.py upgrade
python hostedctl.py rollback backups/<stamp> --previous-release-env previous-release.env --confirm ROLLBACK:<deployment-id>
python hostedctl.py diagnostics diagnostics/<incident-id>
```

Backup briefly quiesces writes and captures PostgreSQL plus object storage with
an integrity manifest. Restore, upgrade, and rollback validate the deployment ID
and require explicit confirmation where destructive. This tooling is for the
dedicated pilot profile; it is not deployment automation or a general supported
self-hosted distribution.

## API errors and correlation

Public errors use stable machine-readable codes and safe messages:

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

Validation errors use the same envelope and place field failures under
`details.fields`. Send or record `X-Request-ID` for support correlation. Stack
traces, provider responses, infrastructure connection details, and secrets are
not returned to clients.

## Remaining technical-preview limitations

- API v1 is versioned but not yet a stable `1.0` compatibility promise.
- The console trusts the deployment boundary and adds no users, login, sessions,
  complex RBAC, or browser credential store.
- Stock Bengali OCR is unavailable; use Unicode text-layer PDFs, TXT, or DOCX for
  Bengali content.
- Provider capability probes occur at process startup; production startup can
  depend on configured provider availability, while routine health polling does not.
- Dedicated operations require an experienced operator and environment-specific
  secret, TLS, backup, monitoring, and recovery controls.
- Connectors, public SaaS tenancy, enterprise identity integration, broad search
  connectors, SDKs, and formal long-term support are not included.

## License

Source code is licensed under the [MIT License](LICENSE). The RAG Builder name,
logo, and branding remain owned by the project owner; the MIT grant does not
grant trademark or brand-use rights.
