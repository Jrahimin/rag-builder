# Learning Documentation



A learning-first knowledge base built alongside the platform. The goal is to

make **AI Engineering** and **platform engineering** concepts understandable,

not just implemented.



## Purpose



APE is a learning-first project: understanding the engineering principles is

considered as important as implementing the features. These documents explain

the *why* and *how* behind the platform — including **which source files**

implement each step.



Each document covers:



- the concept and why it matters,

- a visual / end-to-end journey through the codebase,

- file-by-file walkthroughs,

- trade-offs and production considerations.



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



## Knowledge ingestion pipeline



**Read in this order** for the document upload → parse → chunk pipeline:



| # | Document | What you learn |

| - | -------- | -------------- |

| 1 | [**Knowledge ingestion journey**](./knowledge-ingestion-journey.md) | **Start here** — full E2E, status lifecycle, all key files |

| 2 | [Object storage for RAG](./object-storage-for-rag.md) | Raw + parsed blob storage, keys, `BaseStorageProvider` |

| 3 | [Document parsing and extraction](./document-parsing-and-extraction.md) | Worker, parsers, PyMuPDF, async job queue |

| 4 | [Text chunking for RAG](./text-chunking-for-rag.md) | `ChunkingService`, `document_chunks`, chunks API |

| 5 | [OCR fundamentals](./ocr-fundamentals.md) | Concepts + **what is not built yet** and where OCR will plug in |



Code lives under `backend/app/modules/knowledge/`, `platform/providers/`, `worker/`, and `models/document*.py`.



---



## Retrieval pipeline (after `chunked`)



| # | Document | What you learn |

| - | -------- | -------------- |

| 1 | [Embeddings fundamentals](./embeddings-fundamentals.md) | Providers, `chunk_embeddings`, `embedding_set_version` |

| 2 | [Vector storage and Qdrant](./vector-storage-and-qdrant.md) | Dual storage, payloads, project scoping |

| 3 | [Retrieval feature doc](../features/retrieval.md) | Embed → index → search API, workers, config |



Code lives under `backend/app/modules/retrieval/`, `worker/handlers/{embedding,indexing}.py`.



---



## Planned topics (Phase 1+)



AI and platform capabilities as they are implemented:



- Hybrid retrieval (BM25 + vector + RRF + reranking) — Retrieval v2 (ADR-007)

- Provider abstraction and vendor independence

- Observability, tracing, and cost tracking (Langfuse)

- Evaluation (faithfulness, relevance, toxicity)


