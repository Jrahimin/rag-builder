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
                                        ├──► BaseWebSearchProvider (policy-selected)
                                        └──► build_citation_snapshots
```

| Component | Role |
| --------- | ---- |
| **ConversationService** | Conversation CRUD, list messages |
| **ChatService** | Tx1 user msg → retrieve → prompt → LLM → Tx2 assistant msg |
| **RetrievalPort** | Module-local seam; adapter wraps `SearchService` |
| **ContextBuilder** | Dedupe + budget trim (preserves retrieval order) |
| **PromptBuilder** | Versioned system prompt + separated knowledge/web evidence + history |
| **BaseWebSearchProvider** | Vendor-neutral current-web evidence; never selects the workflow |
| **build_citation_snapshots** | Durable citation JSONB for assistant messages |
| **GroundingService** | Pre-generation evidence decision and post-generation claim/source mapping |

## Data flow

```text
POST /messages
  → validate conversation (active, not deleted)
  → Tx1: persist user message + last_message_at → commit
  → load history + retrieve (read txn rolled back before LLM)
  → GroundingService rank-ordered candidate assessment → indivisible EvidenceUnit selection
  → enforce: insufficient score skips LLM and persists stable reason
  → observe: same diagnostics, selected context still goes to PromptBuilder
  → response_mode selects indexed-only, conditional web fallback, or combined evidence
  → sufficient evidence: PromptBuilder v5 → LLM generate / stream → map claims
  → Tx2: persist assistant (+ claims, citations, metadata, auto-title) → commit
```

LLM failure after Tx1: user message retained, no assistant row.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `LLMConfig` | `APE_LLM__*` | Deployment defaults; per-conversation overrides at create/update |
| `ChatConfig` | `APE_CHAT__*` | Response mode, retrieval top-k, context budgets, history, prompt |
| `WebSearchConfig` | `APE_WEB_SEARCH__*` | Optional OpenAI override plus timeout, result, and evidence bounds; connection/model inherit compatible `APE_LLM__*` values when omitted |
| `RetrievalConfig` | `APE_RETRIEVAL__EMBEDDING_SET_VERSION` | Snapshotted on assistant messages |

`response_mode` defaults to `indexed_only` and is a sparse/versioned Project override. The other
values are `indexed_then_web` and `indexed_and_web`. Notable `ChatConfig` keys also include
`citation_excerpt_max_chars`, `minimum_semantic_evidence_score`,
`evidence_gate_mode` (`enforce` or `observe`), `minimum_reranker_evidence_score`,
`lexical_corroboration_floor_score`, `lexical_corroboration_coverage`,
`minimum_claim_token_coverage`, `candidate_wise_grounding_enabled`, and `include_citations`.

Project revisions may also include a sparse `web_search` section: `enabled`, `model`,
`max_results`, `max_evidence_chars`, `max_output_tokens`, and `request_timeout_seconds`.
Credentials, base URL, and provider backend remain deployment-owned.

## Data model

- `conversations` — config snapshot (`provider`, `model`, `temperature`), nullable `title`, `last_message_at`
- `messages` — no `sequence`; ordered by `created_at`, `id`; assistant `metadata`, `citations`,
  `claims`, `grounded`, and `insufficient_evidence_reason`. `metadata.evidence_gate` records the
  score decision even when `observe` still generates. `metadata.retrieval_trace` includes
  translation status/languages/query/provider and per-candidate branch provenance. Translated query
  text stays in diagnostics only; citations and evidence excerpts remain original chunk text.
  `source_provenance` and `web_search` record the selected source family, fallback use, provider,
  status, and fail-closed errors. Web citations store URL, title, retrieval time, and provider
  separately from Knowledge document/chunk locations.

Candidate-wise diagnostics contain exactly one terminal assessment for each reranked candidate.
An admitted `EvidenceUnit` records deterministic offsets, span derivation, query-variant identity,
and a content hash; context budgeting may omit the whole unit but cannot truncate it. The unit ID
and span hash remain attached to prompt evidence, citations, and claim verification. With the
candidate-wise switch disabled, these decisions run in shadow while the legacy gate remains active.

Soft-deleting a conversation sets `deleted_at` on the conversation only; messages remain for audit.

## API

Prefix: `/api/v1/projects/{project_id}/conversations`

See [conversation API reference](../api/conversation_api.md).

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| Per-conversation LLM snapshot | Reproducible turns; provider resolved per conversation at chat time. Super Admins refresh future messages with `POST .../conversations/{id}/config` after evidence-mode or threshold changes. |
| Tx1/Tx2 split | Avoid holding DB transactions during retrieval/LLM (ADR-008) |
| Retrieval through port | Chat stays decoupled from retrieval internals while supporting hybrid search |
| Messages kept on soft-delete | Audit/history without hard-delete cascade |

## Production note

Chat uses the configured retrieval strategy through `RetrievalPort`. Hybrid retrieval (original
dense + original lexical, optional one translated pair, RRF, optional reranker) is the production
path; semantic search remains available as an explicit rollback or comparison strategy. When rerank
is applied, the evidence gate uses calibrated reranker relevance; otherwise it keeps whole-chunk
cosine plus lexical rescue. `APE_CHAT__EVIDENCE_GATE_MODE=enforce` blocks generation on a failed
score. `observe` records that decision without blocking. Empty retrieval still refuses.

Web-enabled modes never search for document-, metadata-, or `as_of`-scoped requests. Provider
timeouts, failures, and empty results do not permit model-memory fallback. Clear social turns are
handled without an awkward knowledge refusal, while referential follow-ups reuse the prior user
question for retrieval.

The OpenAI adapter requests both Responses web result objects and consulted source URLs. It treats
consulted URLs as discovery only and admits text exclusively from a result object conservatively
associated by provider ID or canonical HTTP(S) URL. Assistant summaries, URL annotations, malformed
URLs, and URL-only results cannot become evidence. Every completed fallback reports one of
`no_sources`, `sources_found_no_extractable_evidence`, `evidence_extracted_irrelevant`, or
`evidence_accepted`; the provider layer performs no fetching, crawling, or page extraction.

## Testing strategy

- Unit: `ChatService` (Tx1/Tx2, refusal, observe/enforce gate, provider resolve, errors, stream cancel),
  `GroundingService`, Gazette evidence-gate comparison (`0.35` / `0.30` / `observe`),
  `ConversationService`, context/prompt builders, citation snapshots, retrieval adapter
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
- [ADR-019](../architecture/adr/019-grounded-response-modes.md)
- [Implementation plan](../plans/conversation_module_plan.md)
- [RAG journey (learning)](../learning/conversation_rag_journey.md)
