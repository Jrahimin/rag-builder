# Conversation Module

Project-scoped RAG chat: retrieve context → evidence gate → prompt/LLM → grounded claims and
citations, or an explicit insufficient-evidence answer without generation.

## Purpose

Complete the RAG user journey on top of the retrieval pipeline. Conversations are stateful; messages persist with durable citation snapshots and execution diagnostics. Chat uses `RetrievalPort`, so it can consume the configured retrieval strategy without importing retrieval module internals.

## Architecture

```text
conversations_router ──► ConversationService (CRUD)
                      └──► ChatService ──► RetrievalPort (composition adapter)
                                        ├──► TurnResolver (one bounded interpretation call)
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
| **ChatService** | Tx1 user msg → bounded turn resolution → retrieve → prompt → LLM → Tx2 assistant msg |
| **TurnResolver** | At most one interpretation call; fallback keeps the raw message and original filters |
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
  → load preceding history (exclusive created_at/id boundary) and capture ORM-safe inputs
  → release the read transaction, then one bounded turn-resolution call when history exists
  → clarification persists/streams with grounded=null and no retrieval
  → otherwise retrieve once with the effective question; original filters unchanged
  → GroundingService rank-ordered candidate assessment → indivisible EvidenceUnit selection
  → enforce: insufficient score skips LLM and persists stable reason
  → observe: same admission and selected units as enforce; veto disabled
    (zero admissions still generate from ranked candidates)
  → response_mode selects indexed-only, conditional web fallback, or combined evidence
  → sufficient evidence: canonical grounding prompt + optional interpretation → LLM
  → Tx2: persist assistant (+ claims, citations, notices, metadata, auto-title) → commit
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
`high_confidence_reranker_evidence_score` (default `0.70`, must stay above the medium
reranker bar), `grounding_mode` (`strict` default or `balanced`),
`lexical_corroboration_floor_score`, `lexical_corroboration_coverage`,
`cross_language_semantic_evidence_score_threshold`,
`minimum_claim_token_coverage`, `store_candidate_trace` (debug; default off), and
`include_citations`. Candidate-wise admission is the only reranked path; when no
reranker applied, the no-reranker fallback uses whole-chunk cosine plus the
cross-language bar and returns the same per-candidate `EvidenceUnit`s.
`strict` still requires an independent semantic, lexical, or cross-language signal on top of
calibrated reranker relevance. `balanced` uses the same medium band. A high reranker band may
admit a calibrated, safely spanned candidate without that corroboration only when
`high_confidence_band_enabled` is on for the active calibration identity. That flag stays off
until a hard-negative run shows `hard_negative_max` below the identity's high bar with a
positive `observed_margin`. Deployment default remains `strict`. Every candidate that
`strict` would admit remains admitted in `balanced` on the same evidence.

Project revisions may also include a sparse `web_search` section: `enabled`, `model`,
`max_results`, `max_evidence_chars`, `max_output_tokens`, and `request_timeout_seconds`.
Credentials, base URL, and provider backend remain deployment-owned.

## Data model

- `conversations` — config snapshot (`provider`, `model`, `temperature`), nullable `title`, `last_message_at`
- `messages` — no `sequence`; ordered by `created_at`, `id`; assistant `metadata`, `citations`,
  `claims`, `grounded`, and `insufficient_evidence_reason`. `metadata.evidence_gate` records the
  score decision even when `observe` still generates. `observe` uses the same admitted selection
  as `enforce`; when nothing is admitted it generates from ranked candidates and records
  `would_have_blocked` / `observe_context=ranked_candidates`. `metadata.retrieval_trace` includes
  translation status/languages/query/provider and per-candidate branch provenance. Per-candidate
  traces are stored on chat messages only when `APE_CHAT__STORE_CANDIDATE_TRACE=true`. Translated query
  text stays in diagnostics only; citations and evidence excerpts remain original chunk text.
  `source_provenance` and `web_search` record the selected source family, fallback use, provider,
  status, and fail-closed errors. Structured `notices` (scope caveat, web evidence used,
  insufficient evidence) are system-rendered metadata, never LLM text or citations. Web citations store URL, title, retrieval time, and provider
  separately from Knowledge document/chunk locations.

Authority redaction of superseded provisions runs **before** admission from
`modifies_expansion_records` on retrieval diagnostics. Hard document scope that excludes an
effective MODIFIES record still answers from admitted scoped evidence and attaches
`scope_excludes_effective_modifier`. There is one canonical grounding prompt; conversation
create/update no longer select a prompt version. `grounded` may be `null` when generation ran
on admitted evidence but the answer had no verifiable claims (for example polarity-only `Yes.`),
or when the turn is a clarification (`finish_reason=clarification`, no retrieval).

Every candidate presented to grounding receives exactly one assessment. Candidates removed
earlier by policy, hydration, or dedup do not need grounding assessments; those removals stay
visible in retrieval diagnostics (`retrieved_count`, `reranked_count`, `removed_count`,
`post_rerank_removed_count`).

An admitted `EvidenceUnit` records deterministic offsets, span derivation, query-variant identity,
and a content hash; context budgeting may omit the whole unit but cannot truncate it. The unit ID
and span hash remain attached to prompt evidence, citations, and claim verification. Retrieved
candidates, admitted evidence, generation context, grounded claims, and user-visible citations stay
separate: not every admitted chunk is a citation. Quality evaluation uses the same
candidate-wise lifecycle through composition.

After the first candidate-wise assessment, high-confidence reranker candidates that only narrowly
miss corroboration may receive a bounded passage-scoring rescue and a reassessment. Rescue cannot
drop a candidate that already passed. Always-on
`retrieval.passage_scoring_enabled` still scores the fused window for evaluation and debugging;
adaptive rescue skips candidates that already have a passage score. Query-token coverage treats
conservative Bangla interrogative/copula scaffolding like English stopwords; it does not stem and
does not encode domain vocabulary.

Authority redaction runs after admission. If the highest-ranked admitted span is removed, selection
continues with the next valid admitted/current unit. Empty context after a non-empty admission is
`failure_stage=context_selection` (`authority_context_empty` or `context_selection_empty`), not an
admission failure.

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
cosine plus lexical rescue. Reranker provider failure stays fail-open to fused order with
`rerank_status=unavailable` and a sanitized `failure_reason` (`timeout` / `rate_limit` /
`connection` / `provider_unavailable` / `unavailable`). The default Cohere rerank timeout is 10
seconds. `APE_CHAT__EVIDENCE_GATE_MODE=enforce` blocks generation on a failed score. `observe`
records that decision without blocking. Empty retrieval still refuses.

Web-enabled modes never search for document-, metadata-, or `as_of`-scoped requests. Provider
timeouts, failures, and empty results do not permit model-memory fallback. Clear social turns are
handled without an awkward knowledge refusal. Referential follow-ups run one
bounded turn-resolution step first; retrieval uses the effective question while
the original message stays the generation user turn. Request filters remain
per-request and non-sticky. Adopted prior results are scenario inputs, not
proof that the previous answer was correct.

The OpenAI adapter requests both Responses web result objects and consulted source URLs. It treats
consulted URLs as discovery only and admits text exclusively from a result object conservatively
associated by provider ID or canonical HTTP(S) URL. Assistant summaries, URL annotations, malformed
URLs, and URL-only results cannot become evidence. Every completed fallback reports one of
`no_sources`, `sources_found_no_extractable_evidence`, `evidence_extracted_irrelevant`, or
`evidence_accepted`; the provider layer performs no fetching, crawling, or page extraction.

## Testing strategy

- Unit: `ChatService` (Tx1/Tx2, refusal, observe/enforce gate, provider resolve, errors, stream cancel,
  combined MODIFIES → grounding → generation → no-web authority path, bounded turn resolution,
  clarification `grounded=null`),
  `TurnResolver` / `turn_resolution` contracts, `GroundingService`, candidate-wise grounding, strict vs balanced modes, adaptive passage rescue,
  captured EN→BN production fail-closed replay, `ConversationService`, context/prompt builders,
  citation snapshots, retrieval adapter
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
- [ADR-020](../architecture/adr/020-authority-notices-canonical-prompt.md)
- [Implementation plan](../plans/conversation_module_plan.md)
- [RAG journey (learning)](../learning/conversation_rag_journey.md)
- [Test RAG journey](./test_rag_journey.md) (`tax_v1` and `business_conversation_v1` fixtures)
