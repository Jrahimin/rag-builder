# Conversation — Memory and Persistence

How multi-turn chat history is stored and replayed to the LLM.

## Tables

| Table | Role |
| ----- | ---- |
| `conversations` | Session metadata, model config snapshot, `last_message_at`, nullable `title` |
| `messages` | Individual turns (`user` / `assistant`); ordered by `created_at`, `id` |

There is **no `sequence` column** — chronological order uses timestamps + deterministic `id` tie-break.

## What gets sent to the LLM each turn

1. Fresh **system** message (template + current context blocks).
2. Last **N** stored `user`/`assistant` pairs from DB.
3. Current **user** question (also persisted in Tx1 before generation).

Older messages remain in DB for the UI but may fall outside the history window.

## Provider / model resolution

| Field | Source |
| ----- | ------ |
| Default `provider`, `model`, `temperature` | `conversations` row (set at create) |
| Per-message override | `messages.provider` / `messages.model` **only if different** from conversation |
| Effective value | `message.field or conversation.field` |

Avoids duplicating the same provider name on every assistant row.

## Auto-title

- Create with `title=null`.
- After first successful assistant reply, title = trimmed first user question (`APE_CHAT__AUTO_TITLE_MAX_CHARS`).
- Manual `PATCH` title is never overwritten.

## Citations and diagnostics

Assistant rows store:

- **`citations`** — `chunk_hash`, ids, optional excerpt (durable across re-index)
- **`metadata`** — `retrieval_time_ms`, `generation_time_ms`, `retrieval_strategy`, etc.

## Related

- [RAG journey](./conversation_rag_journey.md)
- [Conversation module](../features/conversation_module.md)
