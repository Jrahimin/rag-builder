# Feature Documentation

Concise, per-feature reference documentation for the AI Platform Engine.

> **Building an integration?** Start with the
> [Platform Integration Guide](../platform-integration-guide.md).
> **Looking up an `APE_*` key?** Use the [Configuration Map](../configuration-map.md).

## Shipped features

- [Operator Console MVP](operator_console.md)

| Feature | Doc | Scope |
| ------- | --- | ----- |
| Organizations & auth | [organization_module.md](./organization_module.md) | Tenant CRUD, API keys, org-scoped rate limits (ADR-012) |
| Admin users | [admin_users.md](./admin_users.md) | CLI Super Admin bootstrap, console Admin create, disable, soft delete |
| Project Management | [project_module.md](./project_module.md) | CRUD, `is_active` toggle, soft-delete, org scoping |
| Project AI policy and provenance | [project_ai_policy_and_provenance.md](./project_ai_policy_and_provenance.md) | Immutable policy revisions, capabilities, execution snapshots, ownership lock |
| Operator onboarding and source lifecycle | [operator_onboarding_and_source_lifecycle.md](./operator_onboarding_and_source_lifecycle.md) | Client lifecycle, credential handoff/rotation, canonical Project admin, immutable source metadata |
| Knowledge | [knowledge_module.md](./knowledge_module.md) | Upload → parse → chunk (`status=chunked`) |
| Durable jobs | [jobs_module.md](./jobs_module.md) | Transactional dispatch, lease/retry recovery, inspection APIs |
| Safe corpus/index lifecycle | [safe_corpus_index_lifecycle.md](./safe_corpus_index_lifecycle.md) | Immutable full builds, atomic activation/rollback, safe delete/purge, upload validation |
| Hosted integration and delivery | [hosted-integration-commercial-delivery.md](./hosted-integration-commercial-delivery.md) | Signed webhooks, stable v1 contract, hosted profile, recovery operations |
| Production runtime + operator backend | [production_runtime_and_operator_backend.md](./production_runtime_and_operator_backend.md) | Certified profiles, preflight, readiness, metrics, workers, audit |
| Retrieval | [retrieval_module.md](./retrieval_module.md) | Embed → index → semantic + hybrid search (`ready`, ADR-007/009) |
| Conversations | [conversation_module.md](./conversation_module.md) | RAG chat, stateful conversations, SSE streaming (ADR-008) |
| Test RAG journey | [test_rag_journey.md](./test_rag_journey.md) | Local 21-case production-path `tax_v1` corpus, authority, multilingual, and cleanup regression |
| Contextual generation | [contextual_generation.md](./contextual_generation.md) | Caller context → versioned prompt/schema → validated LLM output |
| Multilingual | [multilingual_support.md](./multilingual_support.md) | Unicode tokenization, FTS, OCR notes (ADR-010) |

## Template

Every feature document should cover:

- **Purpose** — what problem it solves and why it exists.
- **Architecture** — components involved and how they fit the layering.
- **Data flow** — the request/processing path (diagram preferred).
- **Configuration** — relevant settings and their defaults.
- **Dependencies** — providers, services, and infrastructure required.
- **Design decisions** — notable trade-offs.
- **Production considerations** — scaling, failure modes, observability.
- **Testing strategy** — how the feature is verified.
- **Future improvements** — known gaps and next steps.

Implementation plans live in [docs/plans/](../plans/) until superseded by feature docs here.
