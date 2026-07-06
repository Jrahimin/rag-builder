# Conversation Module — Implementation Plan

**Status:** Delivered (Phases 0–3 complete, ADR-008).

Three gated phases: **Phase 1** (LLM provider + conversation CRUD), **Phase 2** (RAG chat), **Phase 3** (SSE streaming).

**Prerequisite:** Retrieval Phase 3 baseline (`POST /search` → `RetrievalResult`).

## Layout

| Concern | Location |
| ------- | -------- |
| Module | `backend/app/modules/conversations/` |
| Routes | `backend/app/api/v1/routes/conversations_router.py` |
| DI | `backend/app/dependencies/conversations.py` |
| ORM | `backend/app/models/conversation.py`, `message.py` |

**API prefix:** `/api/v1/projects/{project_id}/conversations`

## Canonical boundary

```text
modules/retrieval/       embed → index → search
modules/conversations/   retrieve (via RetrievalPort) → prompt → LLM → citations
```

Chat never imports retrieval internals; `dependencies/conversations.py` adapts `SearchService`.

## Key design decisions

- **Tx1/Tx2:** commit user message before retrieval/LLM; commit assistant after generation (ADR-008).
- **ContextBuilder:** dedupe + budget trim only; ranking owned by retrieval.
- **Citation snapshots:** `build_citation_snapshots()` separate from context selection.
- **No `sequence` column:** messages ordered by `created_at`, `id`.

## Phases

| Phase | Ships |
| ----- | ----- |
| 0 | ADR-008, architecture doc updates |
| 1 | `BaseLLMProvider`, Conversation/Message CRUD |
| 2 | `ChatService.send_message`, RAG E2E |
| 3 | SSE streaming + client disconnect cancellation |

## Related

- [ADR-008](../architecture/adr/008-chat-on-semantic-baseline.md)
- [Feature doc](../features/conversation_module.md)
- [API reference](../api/conversation_api.md)
