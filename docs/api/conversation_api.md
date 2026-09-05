# Conversation API

RAG chat and conversation management. Hybrid search over `status=ready` documents is the production
path for grounded answers.

**Prefix:** `/api/v1/projects/{project_id}/conversations`

## POST `/`

Create a conversation (`title` optional; auto-set after first answer). Returns **201**.

Conversation creation resolves deployment defaults plus active Project policy and stores an
immutable, secret-free configuration snapshot. Messages reference the active snapshot, so later
deployment or Project changes do not alter prior conversation behavior. `provider`, `model`,
and `temperature` remain deprecated compatibility fields; strict policy
rejects them with `request_policy_override_forbidden`. Prompt version is not selectable;
chat always uses the canonical grounding prompt (`GROUNDED_PROMPT_VERSION`, currently `v8`).

Super Admins can explicitly refresh future messages with
`POST /api/v1/projects/{project_id}/conversations/{conversation_id}/config`, supplying the expected
active snapshot ID and an audit reason. The operation appends a snapshot rather than mutating one.

**Request:**

```json
{
  "title": null,
  "provider": null,
  "model": null,
  "temperature": null
}
```

**Errors:** `unsupported_llm_provider`

## GET `/`

List conversations (paginated). Ordered by `last_message_at` desc.

**Query:** `limit` (default 20, max 100), `offset`, `include_deleted`, `is_active`

## GET `/{conversation_id}`

Get conversation by id.

## PATCH `/{conversation_id}`

Update title or config snapshot. At least one field required; `title: null` is rejected.

**Errors:** `empty_update`, `unsupported_llm_provider`

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

`metadata_filter` values must be strings. `document_id`, `metadata_filter`, and
`as_of` are per-request. Omitted filters are not inherited from earlier turns.

When the conversation has usable history, chat may run one bounded turn-resolution
call before retrieval. The original `content` remains the stored user message and
the generation user turn. Retrieval uses the effective question when resolution
succeeds; original request filters are unchanged. Compact diagnostics are stored
on `assistant_message.metadata.turn_resolution` (`version`, `outcome`, `relation`,
`effective_question`, bindings/provenance, query/filter-change flags, latency,
sanitized `failure_code`). Timeout, malformed JSON, invalid references, and
provider failure fall back to the raw message. Casual turns and turns without
usable history bypass the resolver.

If the turn needs a disambiguating question, the assistant returns
`finish_reason=clarification`, `source_provenance=none`, empty claims/citations,
`grounded=null`, and `evidence_gate.claims_status=not_applicable`. This is
distinct from polarity-only `no_verifiable_claims` and from insufficient
evidence.

**Response `data`:**

```json
{
  "user_message": { "role": "user", "content": "..." },
  "assistant_message": {
    "role": "assistant",
    "content": "...",
    "source_provenance": "knowledge",
    "notices": [],
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
one of: `no_retrieval_results`, `below_relevance_threshold`,
`authority_context_empty`, `context_selection_empty`, or
`low_query_evidence_coverage`. Admission failures stay on `below_relevance_threshold`.
When evidence was admitted but none remained after authority redaction or context
budgeting, the reason is `authority_context_empty` or `context_selection_empty`
and `metadata.evidence_gate.failure_stage` is `context_selection`. `observe` mode still records that assessment on
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

**Errors:** `conversation_not_found`, `conversation_deleted`, `conversation_inactive`, `llm_provider_unavailable` (503)

## POST `/{conversation_id}/messages/stream`

SSE stream (`text/event-stream`). Events:

```json
{"event": "token", "delta": "partial text"}
{"event": "done", "assistant_message_id": "...", "citations": [], "claims": [], "grounded": false, "insufficient_evidence_reason": "no_retrieval_results", "response_mode": "indexed_only", "source_provenance": "none", "web_search": {"status": "not_requested"}, "finish_reason": "insufficient_evidence", "turn_resolution": {"outcome": "standalone", "query_changed": false, "filter_changed": false}}
{"event": "error", "message": "The language model provider is temporarily unavailable."}
```

`done` includes `finish_reason` and a compact `turn_resolution` summary when
resolution diagnostics were recorded (`outcome`, `effective_question`,
`query_changed`, `filter_changed`, `failure_code` / `bypass_reason` when present).
Clarification streams the same `done` shape with `finish_reason=clarification`
and `grounded=null`.

Client disconnect cancels generation best-effort; user message from Tx1 is retained. No assistant row is written when the client disconnects before completion.
