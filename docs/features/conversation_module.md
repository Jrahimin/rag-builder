# Conversation Module

Project-scoped RAG chat: retrieve context → evidence gate → prompt/LLM → grounded claims and
citations, or an explicit insufficient-evidence answer without generation.

## Purpose

Complete the RAG user journey on top of the retrieval pipeline. Conversations are stateful; messages persist with durable citation snapshots and execution diagnostics. Chat uses `RetrievalPort`, so it can consume the configured retrieval strategy without importing retrieval module internals.

## Architecture

```text
conversations_router ──► ConversationService (CRUD)
                      └──► ChatService ──► RetrievalPort (composition adapter)
                                        ├──► ContextBuilder
                                        ├──► GroundingService
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
| **GroundingService** | Pre-generation evidence decision and post-generation claim/source mapping |

## Data flow

```text
POST /messages
  → validate conversation (active, not deleted)
  → Tx1: persist user message + last_message_at → commit
  → load history + retrieve (read txn rolled back before LLM)
  → ContextBuilder → GroundingService evidence gate
  → insufficient: skip LLM and persist stable reason
  → sufficient: PromptBuilder v2 → LLM generate / stream → map claims
  → Tx2: persist assistant (+ claims, citations, metadata, auto-title) → commit
```

LLM failure after Tx1: user message retained, no assistant row.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `LLMConfig` | `APE_LLM__*` | Deployment defaults; per-conversation overrides at create/update |
| `ChatConfig` | `APE_CHAT__*` | Retrieval top-k, context budgets, history window, prompt version |
| `RetrievalConfig` | `APE_RETRIEVAL__EMBEDDING_SET_VERSION` | Snapshotted on assistant messages |

Notable `ChatConfig` keys: `citation_excerpt_max_chars`, `minimum_evidence_score`,
`minimum_query_token_coverage`, `minimum_claim_token_coverage`, and `include_citations`.

## Data model

- `conversations` — config snapshot (`provider`, `model`, `temperature`), nullable `title`, `last_message_at`
- `messages` — no `sequence`; ordered by `created_at`, `id`; assistant `metadata`, `citations`,
  `claims`, `grounded`, and `insufficient_evidence_reason`

Soft-deleting a conversation sets `deleted_at` on the conversation only; messages remain for audit.

## API

Prefix: `/api/v1/projects/{project_id}/conversations`

See [conversation API reference](../api/conversation_api.md).

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| Per-conversation LLM snapshot | Reproducible turns; provider resolved per conversation at chat time |
| Tx1/Tx2 split | Avoid holding DB transactions during retrieval/LLM (ADR-008) |
| Retrieval through port | Chat stays decoupled from retrieval internals while supporting hybrid search |
| Messages kept on soft-delete | Audit/history without hard-delete cascade |

## Production note

Chat uses the configured retrieval strategy through `RetrievalPort`. Hybrid retrieval (BM25 + vector + RRF + reranker) is the production path; semantic search remains available as an explicit rollback or comparison strategy.

## Testing strategy

- Unit: `ChatService` (Tx1/Tx2, refusal, provider resolve, errors, stream cancel),
  `GroundingService`, `ConversationService`, context/prompt builders, citation snapshots, retrieval adapter
- Provider contract: echo LLM + factory overrides
- Integration: `test_conversations_api` (when stack available)

## Future improvements

- Auth/RBAC and rate limiting
- Token accounting on streamed turns
- Langfuse tracing

## Related

- [Retrieval](./retrieval_module.md)
- [ADR-008](../architecture/adr/008-chat-on-semantic-baseline.md)
- [ADR-014](../architecture/adr/014-evidence-quality-and-grounded-answers.md)
- [Implementation plan](../plans/conversation_module_plan.md)
- [RAG journey (learning)](../learning/conversation_rag_journey.md)
