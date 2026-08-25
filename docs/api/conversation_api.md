# Conversation API

RAG chat and conversation management. Hybrid search over `status=ready` documents is the production
path for grounded answers.

**Prefix:** `/api/v1/projects/{project_id}/conversations`

## POST `/`

Create a conversation (`title` optional; auto-set after first answer). Returns **201**.

Conversation creation resolves deployment defaults plus active Project policy and stores an
immutable, secret-free configuration snapshot. Messages reference the active snapshot, so later
deployment or Project changes do not alter prior conversation behavior. `provider`, `model`,
`temperature`, and `system_prompt_version` remain deprecated compatibility fields; strict policy
rejects them with `request_policy_override_forbidden`.

Super Admins can explicitly refresh future messages with
`POST /api/v1/projects/{project_id}/conversations/{conversation_id}/config`, supplying the expected
active snapshot ID and an audit reason. The operation appends a snapshot rather than mutating one.

**Request:**

```json
{
  "title": null,
  "provider": null,
  "model": null,
  "temperature": null,
  "system_prompt_version": null
}
```

**Errors:** `unsupported_llm_provider`, `unknown_prompt_version`

## GET `/`

List conversations (paginated). Ordered by `last_message_at` desc.

**Query:** `limit` (default 20, max 100), `offset`, `include_deleted`, `is_active`

## GET `/{conversation_id}`

Get conversation by id.

## PATCH `/{conversation_id}`

Update title or config snapshot. At least one field required; `title: null` is rejected.

**Errors:** `empty_update`, `unsupported_llm_provider`, `unknown_prompt_version`

## PATCH `/{conversation_id}/status`

Toggle `is_active` (no body).

## DELETE `/{conversation_id}`

Soft-delete conversation. Messages remain in storage for audit; the conversation is hidden from default list/get paths.

## GET `/{conversation_id}/messages`

List messages (ordered by `created_at`, `id`).

**Query:** `limit` (default 50, max 200), `offset`

## POST `/{conversation_id}/messages`

Send a user message; returns grounded assistant answer + citations. Returns **200**.

**Request:**

```json
{
  "content": "What is the refund policy?",
  "document_id": null,
  "metadata_filter": {}
}
```

`metadata_filter` values must be strings.

**Response `data`:**

```json
{
  "user_message": { "role": "user", "content": "..." },
  "assistant_message": {
    "role": "assistant",
    "content": "...",
    "source_provenance": "knowledge",
    "citations": [
      {
        "source_kind": "knowledge",
        "chunk_id": "...",
        "document_id": "...",
        "filename": "policy.txt",
        "chunk_index": 0,
        "page_number": null,
        "char_start": 0,
        "char_end": 120,
        "score": 0.87,
        "chunk_hash": "...",
        "excerpt": "..."
      }
    ],
    "claims": [
      {
        "claim_id": "claim-1",
        "text": "Refunds are accepted within thirty days.",
        "grounded": true,
        "evidence": [
          {
            "citation_index": 1,
            "chunk_id": "...",
            "document_id": "...",
            "filename": "policy.txt",
            "chunk_index": 0,
            "page_number": null,
            "char_start": 0,
            "char_end": 120,
            "excerpt": "..."
          }
        ]
      }
    ],
    "grounded": true,
    "insufficient_evidence_reason": null,
    "metadata": {
      "response_mode": "indexed_only",
      "source_provenance": "knowledge",
      "web_search": {"status": "not_requested", "fallback_used": false},
      "retrieval_time_ms": 120,
      "generation_time_ms": 800,
      "total_time_ms": 950,
      "retrieval_strategy": "hybrid",
      "retrieval_top_k": 10,
      "retrieved_chunk_count": 5,
      "selected_chunk_count": 3
    }
  }
}
```

If retrieval evidence is insufficient, `enforce` mode skips generation and persists a deterministic
answer with `grounded=false`, empty `claims`/`citations`, `finish_reason=insufficient_evidence`, and
one of: `no_retrieval_results`, `below_relevance_threshold`, or
`low_query_evidence_coverage`. `observe` mode still records that assessment on
`metadata.evidence_gate` but continues generation from the already-selected context unless retrieval
returned no chunks.

Resolved Project `response_mode` semantics:

- `indexed_only`: current strict RAG behavior; insufficient evidence returns a friendly no-answer.
- `indexed_then_web`: the same evidence gate runs first. Sufficient Project evidence is used alone;
  otherwise a configured external provider retrieves current web evidence.
- `indexed_and_web`: both paths run and the v5 source-aware prompt receives separately labeled
  evidence. Conflicts must be exposed and cited from both sides.

Web access is suppressed whenever `document_id`, `metadata_filter`, or `as_of` scopes the request.
Provider failure or empty web results fail closed. `source_provenance` is always one of
`knowledge`, `web`, `knowledge_and_web`, or `none`. Web citations use `source_kind=web` and provide
`web_url`, `web_title`, `web_retrieved_at`, and `web_provider`; Knowledge location fields are null.

**Errors:** `conversation_not_found`, `conversation_deleted`, `conversation_inactive`, `unknown_prompt_version`, `llm_provider_unavailable` (503)

## POST `/{conversation_id}/messages/stream`

SSE stream (`text/event-stream`). Events:

```json
{"event": "token", "delta": "partial text"}
{"event": "done", "assistant_message_id": "...", "citations": [], "claims": [], "grounded": false, "insufficient_evidence_reason": "no_retrieval_results", "response_mode": "indexed_only", "source_provenance": "none", "web_search": {"status": "not_requested"}}
{"event": "error", "message": "The language model provider is temporarily unavailable."}
```

Client disconnect cancels generation best-effort; user message from Tx1 is retained. No assistant row is written when the client disconnects before completion.
