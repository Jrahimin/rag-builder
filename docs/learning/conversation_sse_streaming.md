# Conversation — SSE Streaming

Token-by-token responses on `POST .../conversations/{id}/messages/stream`.

## Why streaming

Users see partial output while the LLM generates. Generation still runs **inside the HTTP request** (not a background job) — same Tx1/Tx2 model as non-streaming chat.

## Event format

```text
data: {"event": "token", "delta": "partial"}

data: {"event": "done", "assistant_message_id": "...", "citations": [...]}

data: {"event": "error", "message": "..."}
```

Content-Type: `text/event-stream`.

## Flow

```text
Tx1 — persist user message, commit
Stream — ContextBuilder → PromptBuilder → llm.stream()
Collect deltas → yield SSE token events
Tx2 — persist full assistant + citations, commit
Yield done event with citations
```

## Cancellation

If the client closes the connection:

1. Router checks `Request.is_disconnected()` between chunks.
2. Stops consuming `llm.stream()`; provider closes HTTP stream.
3. User message from Tx1 **remains**; assistant row omitted if Tx2 did not complete.

Providers must propagate `asyncio.CancelledError` (see `BaseLLMProvider` contract).

## Related

- [Conversation API](../api/conversation_api.md)
- [Provider integration](./conversation_provider_integration.md)
- [ADR-008](../architecture/adr/008-chat-on-semantic-baseline.md)
