# Architecture Decision Records (ADRs)

ADRs document significant architectural decisions with context, trade-offs, and
consequences. Each ADR is the **long-term record** for its decision; narrative
guides in this folder elaborate on implementation.

| ADR | Title | Status |
| --- | ----- | ------ |
| [ADR-001](./001-modular-monolith.md) | Modular monolith with platform kernel | Accepted |
| [ADR-002](./002-project-centric-ownership.md) | Project as central aggregate | Accepted |
| [ADR-003](./003-provider-abstraction.md) | Provider abstraction layer | Accepted |
| [ADR-004](./004-configuration-hierarchy.md) | Three-tier configuration model | Accepted |
| [ADR-005](./005-background-processing-arq.md) | Taskiq for background jobs | Accepted |
| [ADR-006](./006-deployment-topology.md) | API + worker process separation | Accepted |
| [ADR-007](./007-staged-retrieval-delivery.md) | Staged retrieval delivery (semantic baseline → hybrid v2) | Accepted |
| [ADR-008](./008-chat-on-semantic-baseline.md) | Chat v1 on semantic baseline; split transactions; RetrievalPort | Accepted |
| [ADR-009](./009-retrieval-v2-hybrid-search.md) | Retrieval v2 hybrid search; RetrievalContext; RRF; RerankRequest | Accepted |
| [ADR-010](./010-multilingual-document-processing.md) | Multilingual document processing; Unicode tokenization; OCR; reindex CLI | Accepted |
| [ADR-011](./011-parser-quality-scoring-and-pdf-fallback.md) | Parser quality scoring and PDF fallback | Accepted |
| [ADR-012](./012-organization-api-key-auth.md) | Organization API key authentication | Accepted |
| [ADR-013](./013-postgresql-native-semantic-retrieval.md) | PostgreSQL-native semantic retrieval with pgvector | Accepted |

## When to add an ADR

- Choosing between architectural alternatives
- Changing dependency rules or module boundaries
- Selecting infrastructure (queue, vector DB, etc.)
- Changing configuration precedence or ownership model

## Format

Each ADR includes: **Context**, **Decision**, **Consequences**, **Alternatives considered**.
