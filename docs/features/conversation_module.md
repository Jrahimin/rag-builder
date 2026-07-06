# Conversation Module

Project-scoped RAG chat: retrieve context → build prompt → LLM → grounded answer + citations.

## Purpose

Complete the RAG user journey on top of the retrieval semantic baseline. Conversations are stateful; messages persist with durable citation snapshots and execution diagnostics.

## Architecture

```text
conversations_router ──► ConversationService (CRUD)
                      └──► ChatService ──► RetrievalPort (composition adapter)
                                        ├──► ContextBuilder
                                        ├──► PromptBuilder
                                        ├──► BaseLLMProvider (per-conversation resolve)
                                        └──► build_citation_snapshots
```

| Component | Role |
| --------- | ---- |
| **ConversationService** | Conversation CRUD, list messages |
| **ChatService** | Tx1 user msg → retrieve → prompt → LLM → Tx2 assistant msg |
| **RetrievalPort** | Module-local seam; adapter wraps `SearchService` |
| **ContextBuilder** | Dedupe + budget trim (preserves retrieval order) |
| **PromptBuilder** | Versioned system prompt + context + history |
| **build_citation_snapshots** | Durable citation JSONB for assistant messages |

## Data flow

```text
POST /messages
  → validate conversation (active, not deleted)
  → Tx1: persist user message + last_message_at → commit
  → load history + retrieve (read txn rolled back before LLM)
  → ContextBuilder → PromptBuilder → resolve LLM from conversation snapshot
  → LLM generate / stream
  → Tx2: persist assistant (+ citations, metadata, auto-title) → commit
```

LLM failure after Tx1: user message retained, no assistant row.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `LLMConfig` | `APE_LLM__*` | Deployment defaults; per-conversation overrides at create/update |
| `ChatConfig` | `APE_CHAT__*` | Retrieval top-k, context budgets, history window, prompt version |
| `RetrievalConfig` | `APE_RETRIEVAL__EMBEDDING_SET_VERSION` | Snapshotted on assistant messages |

Notable `ChatConfig` keys: `citation_excerpt_max_chars`, `auto_title_max_chars`, `include_citations`.

## Data model

- `conversations` — config snapshot (`provider`, `model`, `temperature`), nullable `title`, `last_message_at`
- `messages` — no `sequence`; ordered by `created_at`, `id`; assistant `metadata` + `citations` JSONB

Soft-deleting a conversation sets `deleted_at` on the conversation only; messages remain for audit.

## API

Prefix: `/api/v1/projects/{project_id}/conversations`

See [conversation API reference](../api/conversation_api.md).

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| Per-conversation LLM snapshot | Reproducible turns; provider resolved per conversation at chat time |
| Tx1/Tx2 split | Avoid holding DB transactions during retrieval/LLM (ADR-008) |
| Semantic baseline via port | Retrieval v2 swaps adapter only |
| Messages kept on soft-delete | Audit/history without hard-delete cascade |

## Production note

Chat v1 uses **semantic retrieval** via `RetrievalPort`. Hybrid retrieval (Retrieval v2) is a drop-in adapter upgrade (ADR-008).

## Testing strategy

- Unit: `ChatService` (Tx1/Tx2, provider resolve, errors, stream cancel), `ConversationService` validation, `ContextBuilder`, `PromptBuilder`, citation snapshots, retrieval adapter
- Provider contract: echo LLM + factory overrides
- Integration: `test_conversations_api` (when stack available)

## Future improvements

- Retrieval v2 hybrid adapter
- Auth/RBAC and rate limiting
- Token accounting on streamed turns
- Langfuse tracing

## Related

- [Retrieval](./retrieval_module.md)
- [ADR-008](../architecture/adr/008-chat-on-semantic-baseline.md)
- [Implementation plan](../plans/conversation_module_plan.md)
- [RAG journey (learning)](../learning/conversation_rag_journey.md)
