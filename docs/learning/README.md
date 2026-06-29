# Learning Documentation

A learning-first knowledge base built alongside the platform. The goal is to
make **AI Engineering** and **platform engineering** concepts understandable,
not just implemented.

## Purpose

APE is a learning-first project: understanding the engineering principles is
considered as important as implementing the features. These documents explain
the *why* and *how* behind the platform.

Each document covers:

- the concept and why it matters,
- internal flow and architecture,
- trade-offs and alternatives,
- production considerations and common mistakes.

---

## Foundation sprint (start here)

Documents explaining what was built in the platform foundation:

| Document | Topic |
| -------- | ----- |
| [Foundation Sprint Overview](./foundation-sprint-overview.md) | What exists, what was skipped, reading order |
| [Application Factory and FastAPI](./application-factory-and-fastapi.md) | `create_app`, lifespan, DI, versioning, error handling |
| [Configuration System](./configuration-system.md) | Pydantic Settings, `APE_*` env vars, Docker vs local |
| [Structured Logging](./structured-logging.md) | structlog, `request_id` / `trace_id`, JSON vs console |
| [Database and Migrations](./database-and-migrations.md) | Async SQLAlchemy, sessions, Alembic, repositories |
| [Entity Lifecycle and Reusability](./entity-lifecycle-and-reusability.md) | Mixins, repositories, service helpers, request walkthroughs |
| [Docker Local Development](./docker-local-development.md) | Compose stack, health checks, volumes, hybrid workflow |
| [Testing Strategy](./testing-strategy.md) | Pytest layout, fixtures, unit vs integration |

For request flow and layering, see
[docs/architecture/module-architecture.md](../architecture/module-architecture.md).

---

## Planned topics (Phase 1+)

AI and platform capabilities as they are implemented:

- RAG fundamentals and the ingestion → retrieval → generation lifecycle
- Chunking strategies and their trade-offs
- Embeddings and vector indexing
- Hybrid retrieval (BM25 + vector + RRF + reranking)
- Provider abstraction and vendor independence
- Background processing and job queues (Arq)
- Observability, tracing, and cost tracking (Langfuse)
- Evaluation (faithfulness, relevance, toxicity)
