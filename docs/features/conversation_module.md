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
  → resolve request/message source scope; indexed_only centrally suppresses web and web reuse
  → incomplete evidence may enter one shared-deadline focused or broad recovery budget
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
`include_citations`. Bounded recovery is controlled by
`bounded_recovery_enabled` (enabled by default): focused lookup uses 15 seconds and up to two initial queries with no
follow-up; broad coverage/current-rule/calculation work uses 30 seconds, up to four initial and two
follow-up queries, and one follow-up round. These values are configurable through the corresponding
`APE_CHAT__*_RECOVERY_*` variables or the Project AI configuration screen. Disabled retains the legacy recovery limits;
immutable snapshots created before this policy remain disabled when replayed.
The same seven controls are revisioned per Project under canonical V2 execution policy; resolution
records each origin, hashes the effective values, and copies them into the immutable conversation
snapshot. Older snapshots that lack the fields replay with bounded recovery disabled and the
documented default budgets.
Candidate-wise
admission is the only reranked path; when no
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
  status, and fail-closed errors. `source_scope` records the requested/effective scope and its
  origin/reason; `verification_version` prevents historical verification results from being silently
  reinterpreted. Structured `notices` (scope caveat, web evidence used,
  insufficient evidence) are system-rendered metadata, never LLM text or citations. Web citations store URL, title, retrieval time, and provider
  separately from Knowledge document/chunk locations.

Authority redaction of superseded provisions runs **before** admission from
`modifies_expansion_records` on retrieval diagnostics. A MODIFIES edge and target provision
identify a relationship and scope, but do not establish whether an amendment replaces the whole
provision. Redaction requires both `provision_effect=replaces` and
`replacement_scope_verified=true`; without that proof, base text is retained and the affected
passage is marked unresolved. Current relationship metadata does not yet produce those fields, so
current-rule answers affected by such edges remain guarded. Hard document scope that excludes an
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

Web-enabled modes never search for document-, metadata-, `as_of`-, or corpus-only scoped requests.
The message request can explicitly set `source_scope=indexed_only`; clear English/Bangla corpus-only
wording has the same one-turn effect. Provider
timeouts, failures, and empty results do not permit model-memory fallback. Clear social turns are
handled without an awkward knowledge refusal. Referential follow-ups run one
bounded turn-resolution step first; retrieval uses the effective question while
the original message stays the generation user turn. Request filters remain
per-request and non-sticky. Adopted prior results are scenario inputs, not
proof that the previous answer was correct.

When bounded recovery is enabled, one monotonic deadline covers planning, queue waits, retrieval,
structured-output retries, review, and the permitted follow-up. Deadline expiry can restore an
already validated partial checkpoint, while client cancellation propagates and stops the turn.
When an unscoped, non-calculation turn has no authority-safe indexed passage for a broad compliance
overview, bounded recovery gets its configured opportunity first. If it reaches its deadline
without validated proof, a web-enabled turn may try reviewed web evidence. Web search and review
share the evidence-gathering budget; expiry is reported as `search_timeout` or `review_timeout`.
Unreviewed indexed passages are never promoted. Scoped requests, current-rule applicability checks,
calculations, and provider failures retain their guard. A local recovery deadline without validated
proof is persisted as an `insufficient_evidence` response; a genuine provider timeout remains a
failed execution. If one candidate quote is invalid but another candidate has an independently
verified exact quote, the verified source remains admissible and the nested
`scope_review.status` is `reviewed_partial`; the outer `web_search.status` remains
`evidence_accepted` when evidence was admitted. Web-only compliance overviews use an explicit
partial-coverage notice in both web-enabled modes. The search query includes the resolved question
and reference date, with selected Project scope context when available. Claim verification separates
an "and if" continuation only
when it introduces its own consequent; a second condition on the same assertion remains required.
Diagnostics distinguish deadline expiry, provider failure, no-new-evidence stops, attempted and
unattempted requirements, and actual source provenance. A nested provider timeout retains its
provider identity, error code, and safe context even when a validated partial checkpoint is restored;
it is not reported as expiry of the recovery-owned deadline.

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


### Bounded evidence timing and mixed-source discovery

Local recovery, web search and relevance review run sequentially. Their evidence-phase
ceiling adds their allowances, starting after initial retrieval; search reserves 20 seconds
for source review. Project recovery budgets must include planning, retrieval and coverage
validation, not just one model call. Existing conversations retain their configuration
snapshots, so verify a Project change in a new conversation or explicitly refresh the snapshot.

GPT-6 Luna uses low reasoning for bounded planning, structured retries, coverage/input
review, turn resolution and web relevance review; final answer generation keeps the provider
default. Its web-search evidence call also uses low reasoning. This reduces internal-token
exhaustion without changing quote validation, source scope or claim verification. Other
models keep their existing parameters. See the [model's supported reasoning settings](https://developers.openai.com/api/docs/models/gpt-6-luna).

Discovery language is selected by the relevant source concept; the newest recalled source
does not define the language of every question. Explicit Act/work names may be retained to
avoid retrieving unrelated laws. Draft documents remain excluded; missing or unreviewed
Companies Act material requires reviewed web evidence or a separately verified corpus update.
Web search has its own progress span and timeout wording.


### Compliance QA refinements (2026-09-26)

Broad bounded recovery interleaves central-duty searches with applicability searches before applying the query cap. This prevents a collection of possible exemptions or conditional permits from consuming every search slot. Focused and calculation recovery keep their existing priority order, and all retrieved rules still need validated applicability proof.

The coverage prompt distinguishes an actual potentially applicable exemption from the mere statutory power to issue exemptions. It preserves independently supported general rules with explicit limits. This is not permission to disregard a cited exemption or conflicting amendment. Optional web supplementation of an already reviewed partial answer is capped at 15 seconds; web-only recovery retains its configured timeout. Provider rate limiting remains a possible external failure.

Prompt v19 reinforces citations on opening factual summaries and relevant qualifications on filing deadlines. Currency verification recognizes Bangla numeric spell-outs, complete hundred expressions, and explicit numeric scales (including parenthetical spell-outs). Conflicting spell-outs and truncated components of larger spelled amounts remain rejected; semantic support is still required.

Local corpus maintenance for Income Tax & Budget added full official Bangladesh Laws consolidated statutory-body snapshots for the Companies Act 1994 and Income Tax Act 2023, with original HTML, source URL, retrieval date, and extraction hashes in `artifacts/qa-timeout-repair`. Separate schedules not on the print pages are explicitly excluded. Retrieval date is not commencement, and these current snapshots must not be treated as historical editions. Draft incomplete English company sources were not activated. This is local data maintenance, not seeded production data or a migration.


### Partial review continuity and provision locators

A delta coverage review evaluates only changed or unresolved requirements, but its
partial answer scope spans the whole turn. It can retain previously validated duties
alongside newly proven duties; final handoff still verifies all selected source quotes
and authority dependencies. Interacting calculation dependencies remain inseparable.
Generation copies provision identifiers from the governing heading rather than an
incidental cross-reference. The monetary claim guard excludes explicit section/article
locators, while still checking currency amounts. This does not independently certify
a legal reference; source applicability and claim verification remain required.

For imported statutes, preserve exact provision numbers in source headings so
continuation chunks retain their parent provision. Corrected representations use
new source revisions and normal indexing rather than mutating historical chunks.
Latency depends on the project recovery budget and provider timings; recovery timeouts
are not an end-to-end response SLA.


### Reviewed scope and bounded synthesis

Coverage checks may carry an explicit `answerable_scope`: the precise duty or rule
that the reviewer judges independently useful. The handoff merges these scopes into
the partial-answer IDs, then requires exact source proof for every selected ID.
`supported` alone does not authorize a partial calculation; unresolved governing
continuations cannot authorize a scope. Missing rules and exclusions remain visible.
A validated partial answer is handed off when less than 12 seconds remain in the
recovery budget, instead of beginning retrieval plus another review that is unlikely
to finish. This is a recovery scheduling policy, not a guaranteed request SLA.

`indexed_then_web` falls back when indexed evidence blocks generation; a useful
validated indexed partial answer does not automatically pay for another web review.
`indexed_and_web` retains its explicit combined-source behavior.

For OpenAI GPT-6 Luna, internal JSON review purposes use JSON mode, with application
schema validation and exact-source checks still required. JSON mode guarantees
syntax, not schema correctness. Luna request-scoped reasoning is bounded to low for
internal reviews and final source-based synthesis; requests outside these known
purposes keep provider defaults. Final responses remain ordinary prose/Markdown.
Bengali currency and duration checks recognize complete spelled monetary values,
records/notices/member-list subjects and legal locators without equating fines to
fees or one duty's deadline to another's.

Independent authoritative overviews with six or more requirements split the first
coverage review into two concurrent requirement partitions. Both receive identical
source text, source-version limitations and the original question. Each partition
must use its assigned IDs; unknown or duplicate identities cannot prove a duty.
Missing or ambiguous IDs become explicit unverified gaps, preserving independently
reviewed exact-ID checks instead of reporting a provider outage. The union still
undergoes exact proof and scope validation. Cancellation stops both calls. This reduces serial output latency
at the cost of sending the shared input twice. Calculations, current-applicability
and inherited-scope revalidation retain a single interacting-rule review.

Authoritative compliance answers do not use unreviewed admitted-evidence fallback:
browser QA showed that this could apply a valid rule to the wrong company category.
Only validated complete or independently reviewed partial proof can open that gate.
Streaming propagates the same provider-call purpose as regular requests only while
advancing the provider, without leaking context across transport yields.

Broad recovery budgets focused query results round-robin before unreviewed initial
broad hits, so a duty's second governing passage is not crowded out by generic
context. Existing proof is revalidated against the final evidence packet. Broad
answers prefer individually cited short sentences over compound table rows and
keep reviewed coverage limitations separate from legal assertions.

Batched recovery permits up to four concurrent searches per request (eight searches
in two waves), retaining separate database sessions, pinned snapshot validation,
ordered results, cancellation cleanup and the existing shared process/deployment
limits. This is a latency optimization, not an increase to query or evidence budgets.

For ordinary present-day overviews, review can establish a continuing duty from the
operative text of an active primary official consolidated statute without demanding
a separate commencement date for each unchanged section. Null effective-date metadata
alone is not a temporal conflict. Historical/future questions, period-specific rates,
textual commencement conditions and identified amendments/conflicts still require
applicable temporal proof; source labels never substitute for operative rule text.

Current-rule coverage also applies to direct questions about changing facts such as
rates, limits, thresholds, exemptions, deadlines, fees and penalties, even when the
user does not say “current.” Broad policy summaries do not automatically take this
route. These focused questions use the bounded focused lookup allowance (15 seconds,
up to two initial queries and no follow-up); broader coverage and calculation work
keeps the broad allowance. For a rate or threshold, recovery searches the governing
category and period heading together with the operative rule or table, may try the
source-language terms, and stays within that allowance. A proposed table is not proof
of the operative value.

Turn resolution preserves an explicit historical period's kind and years, including
assessment, fiscal, financial, calendar and named single-year periods. A rewrite may
not drop or substitute the user's explicit period; it outranks conflicting history or
Project defaults. Newly introduced year ranges must be attested by the message, an
active prior binding, or domain instructions. Abbreviated ranges such as `2025-26` are
checked as one period, while ISO dates are not treated as year ranges. Monetary claim
checks recognize South Asian digit grouping and explicit lakh/lac/crore scales in
English and Bangla (for example, `1,25,000`, `2 lakh`, or `৳ 1.5 কোটি`). Bare source
numbers count as money only with a nearby monetary cue; years,
percentages, counts and durations cannot supply a claimed amount.

When indexed evidence leaves authority unresolved, reviewed web evidence cannot
override that unresolved local authority. This remains guarded through recovery and
applies even when a web-enabled Project response mode is configured; other eligible
unscoped, non-calculation turns may still use the documented reviewed web fallback.

Claim locator selection excludes isolated headings identified by the parser's
heading_path when body text exists. The full passage still retains its governing
headings and exact offsets. This prevents a matching document title from displacing
the operative clause. Quantity binding recognizes record/voucher and alternative
storage-location phrases across English and Bengali without changing durations.

Generation includes a compact fixed citation index in supplied passage order. Source
labels in that index remain untrusted data; platform instructions follow it. Models
must not renumber markers by order of appearance or create a competing bibliography.
Citation validation still checks the resulting claims against the actual indexed
passages; prompt instructions do not count as proof of citation correctness.

Evidence-limit statements referring to reviewed provisions are classified as coverage
statements and still require a matching structured coverage verdict. They cannot
establish that a legal duty or sanction does not exist.


### Conditional detail and recovery diagnostics (September 2026)

Authoritative planning treats requested details qualified by "if available" or
"only if supported" as optional corroboration unless an identified exception or
conflict affects the principal rule. Missing optional detail must not block an
independently evidenced answer. Period applicability and actual conflicting
amendments still require proof. Coverage and generation distinguish ordinary due
dates from conditional late-filing windows and category-specific exceptions.
Canonical generation prompt provenance is `v24`; authoritative recovery provenance
is `v28-conditional-facets-and-recovery-progress`.

`metadata.knowledge_repair.requirement_progress.attempts` preserves executed
search attempts even when coverage review times out or a provider fails. The Test
Lab also reads `requirement_attempts` for older saved messages whose progress
summary was incomplete. An executed search is not proof that its result was
reviewed or that the answer is grounded.

Within a paragraph or list item, citations bind backward to the preceding uncited
sentence run, stopping at the previous explicit citation. A later uncited limitation
does not erase that association. Citations do not cross paragraphs or list items,
and each inherited claim still undergoes normal evidence verification. Wording such
as "these passages do not establish" is evaluated against structured coverage gaps;
it is not automatically accepted as a verified factual claim.

Claim verification scopes the negative fallback conditions in the Bangla Companies
Act section 190 filing sentence separately from its positive filing duty. A missing
AGM or listed officer does not by itself contradict a cited filing deadline or
signing requirement. Direct reversals of the duty and an affirmative-officer claim
for the director fallback remain unsupported.
Qualified negative officer conditions (for example, officers being unwilling or
unavailable) remain unverified rather than being equated with having no officers.

Authoritative scope review recognizes an explicit Bangla private-company category
in quoted proof. A generic company rule still does not establish a private-only
rule. If planning supplies no discovery queries but an initial partial answer has
already passed exact quote, source-range and authority validation, recovery hands
off that reviewed partial scope with its unresolved duties visible.
Evidence-limit prose using "the reviewed passage" and a same-paragraph "it also"
continuation is checked against structured coverage gaps. An exemption assertion
is not accepted as such a continuation.

### Fulfillment-aware source review and citation display

Fresh coverage checks report source `supported` separately from `fulfillment`
(`full`, `partial`, or `none`) and list `unresolved_facets`. Exact quotation alone
does not establish that a requested period, company category, ordinary deadline,
conditional filing window, or applicability condition was answered. Complete
coverage requires full fulfillment and valid proof for every required check.
Legacy checks without fulfillment retain an unknown value. Independently useful
partial scopes and their proof IDs reach generation as a reviewed checklist;
unfinished adjacent context and dependent calculations remain blocked.
Partially fulfilled duties keep their cited source available for an answer while
remaining eligible for discovery and visible as unresolved coverage gaps.
The verifier keeps checking partially answered duties in later evidence reviews.
A generic filing interval cannot close a named-year deadline until cited proof
establishes that period and its applicability. Grounding checks factual clauses
that follow verification advice and matches explicit source limitations to
reviewed coverage gaps.
For a focused numeric rate or threshold, a partial answer also requires fully
fulfilled proof for both the central rule and governing category/period. If either
dependency is absent or unresolved, it cannot be presented as a supported partial
value; separate explicit topics may still retain independent partial answers.

Initial recovery searches remove section anchors the user did not supply before
deduplication. Diagnostics retain original and executable queries, ownership,
review-input chunk IDs, branch candidates, and review protocol errors. A malformed
review ID receives the existing bounded format correction; unresolved identities
remain review-incomplete and do not alone trigger more retrieval.

The Test Lab citation preview uses evidence-unit offsets when available. For
reconstructed context, document coordinates are labelled as an envelope; missing
local evidence offsets yield unknown evidence length. Stored coordinates and hashes
are unchanged.


### Temporal evidence and task-scoped recovery (2026-09-29)

Conversation verification now shares a source-preserving quantity model for claim
and evidence money checks. It recognizes money, rates, periods, legal locators,
counts, durations and untyped numbers, retains each original text span, and uses
decimal values for normalized amounts. Periods are classified from surrounding
syntax rather than their numeric value alone, so an explicit `Tk 2026` remains a
money value. This classification supports later claim checks; it does not establish
that a value belongs to the governing rule or period.

Authoritative planning asks for a task kind (`rule_lookup`, `personal_eligibility`,
`calculation`, or `comparison`) and explicit dependencies for each requirement.
Partial-answer validation follows those dependencies transitively. It can retain a
fully proved independent topic while withholding a claim whose own category or
period dependency is unresolved. A general rule lookup does not by itself establish
personal entitlement.

The plan validator rejects unknown or cyclic requirement dependencies before
retrieval. A request to explain which facts determine eligibility remains a rule
lookup unless the user asks for a conclusion about a particular person. Focused
follow-up planning receives canonical missing requirement IDs with descriptions;
unbound queries are still rejected, but explanatory gap text is not treated as an
ID. Test Lab distinguishes a recovery deadline from a completed evidence gap in
its answer inspector. Neither state authorizes an answer without reviewed proof.
For numeric rules, a partial answer retains only fully proved requirements and
their governing category/period dependencies. Generation is instructed to state
only those reviewed rules and to put unresolved eligibility details in one
coverage-limitation sentence. The deterministic claim check remains the final
grounding signal for generated text. If a reviewed partial answer generates an
unverified paragraph, that paragraph is removed and the retained text is checked
again; when no supported answer remains, the response is withheld. Streaming
partial answers are delivered only after this check, so provisional unsupported
text is not shown to the user.
The quantity checker recognizes digit-spelled Bengali decimal amounts in
parentheses (for example, a lakh-denominated cap) and keeps enumerated formula
lines attached to their preceding governing clause without counting footnote
numbers as monetary evidence.
When the active index is dominated by a source language different from the
question language, the recovery planner is instructed to spend one bounded
initial query on the governing rule in that source language. A matching search
hit remains discovery only until normal source and proof checks pass.

Recovery can use existing adjacency retrieval to complete an admitted passage when
source metadata reports that heading or table context exceeded the context budget.
This is a bounded structural attempt, separate from semantic query expansion, and
keeps the original project, document, metadata, period and snapshot scope. A
continuation is still subject to exact source proof and authority review. Current
regression coverage now passes for grouped-decimal arithmetic, rejecting a wrong
calculation result when only its rate is cited, and attempting adjacency after table
context overflow. The follow-up type corrections pass the full backend unit and
architecture suite, Ruff format/lint checks, and mypy. Focused live QA in the
active Income Tax project reached a cited partial answer for the current individual
rebate formula with all three factual claims supported by claim verification.
Eligibility details still depend on separate source proof. No performance improvement or
completion target has been established.

Claim verification now gates every streamed factual answer. Missing citation
markers can be repaired only after an uncited claim maps to the selected source;
the repaired answer is verified again. Any remaining ungrounded answer is
replaced with fully reviewed, cited requirement scopes only when those scopes
pass the same claim verifier; otherwise it is withheld with a distinct
`claim_verification_failed` reason. For a missing
heading or preceding rule, bounded adjacent recovery can inspect a top hit from
a second retrieved document as well as the reviewer's cited document. This
helps when the cited passage is a clipped continuation from a different work;
both routes still pass source policy, relevance admission, and exact proof checks.
For a rate schedule, a reviewed passage whose only matching year is a document
title or page footer cannot establish the operative year. The reviewer can
inspect an earlier retrieved passage from the same work when a heading is
missing, while keeping the same bounded adjacency and source checks.
For a tax-free threshold, claim verification binds the amount to the first
zero-rate row in a cited Bangla or English rate table; later rate bands cannot
validate that amount. Questions asking what a named procedure mentions are
reviewed against its stated steps, documents, and fee references without
assuming an exhaustive document list or an unstated fee amount.
When a later complete source review replaces an inadequate discovery passage,
the earlier passage-only objection no longer forces a partial answer. Other
missing duties remain open until separately proved.


## Shared request and proof contract

Every factual turn carries request.scope.v1 with task, legal periods, exact snapshot date and source restrictions. An assessment/fiscal period never becomes an invented timestamp. Conditions are semantic facets with source quotations. Complete reviewed headings, rows and exceptions survive into citation supporting_spans; authority and evidence support remain separate. Structured answer drafts may reference approved requirement/proof IDs and are rendered before shared POST/SSE/GET verification. Recovery is capped at three targeted queries and two structural batches. The monotonic outer request window is 60 seconds; work stops by 50 seconds and recovery by 30 seconds, preserving generation, verification and persistence reserves. Deadline or incomplete proof yields a precise limitation; latency percentiles require controlled repeated measurements.


### Structured answer and terminal result contract

Reviewed factual evidence now selects one canonical internal AnswerDraft protocol
(answer.draft.v1). Its segments carry text, requirement_ids and immutable chunk
proof_ids. The prompt, provider output intent and parser use the same schema;
the renderer alone creates visible citation numbers. Compatible providers receive
JSON mode, Gemini/Ollama receive their schema parameters, and unknown/legacy
adapters receive a portable JSON schema prompt. Application schema, exact quotation,
semantic entailment, arithmetic and authority checks remain mandatory on every path.

At most one budgeted shape correction may repair a malformed draft against the same
proof set. It may not change the original assertions, requirement IDs or proof IDs. Invalid/foreign proof IDs
are rejected without treating retrieved law as missing. Condition facets are always
an array of typed who/action/when/condition/evidence_indexes records. A reviewed
heading may compose with a dependent row only inside the same attested proof
boundary; every contributing original span survives into the proof handoff.

RequestScope distinguishes user scenario stipulations from legal evidence and
personal eligibility tasks. Specified-person rule lookups do not require proving
the user's own membership. AY/FY labels match typed identities; adjacent labels do
not overlap just because their numeric endpoints touch. Explicit legal-period
ranges require period_mode=range. Multi-period requests preserve earlier editions
when a replacement covers only a later requested period.

One monotonic request deadline provides a 60-second outer window. Normal work ends
by 50 seconds, recovery by 30 seconds, reserving 10 seconds each for generation,
claim verification and deterministic terminal persistence. Action admission reserves
subsequent validation; query deduplication also includes normalized scope, unresolved
IDs, build/source identity, anchor IDs and evidence hashes. Actions become completed
only after validated completion; cancelled correction remains cancelled.

The additive terminal_outcome (answer.outcome.v1) is persisted in message metadata,
exposed in MessageResponse and sent in the SSE done event. It distinguishes answered,
partial, needs_input, insufficient_evidence, unresolved_authority, verification_failed
and timed_out. HTTP success only establishes message delivery. Internal schema
failure does not ask the user to narrow a valid question. Deterministic nonfactual
terminal templates skip LLM semantic review; factual partial answers still undergo
all checks. lifecycle is the shared diagnostics field on normal and timeout paths;
persistence_completed records successful persistence separately from the timing
snapshot's snapshot_includes_persistence flag.

Operator Test Lab shows typed outcome labels and bounded error paths/counts. Failed
candidate assertions and raw model drafts are excluded from the entire public response,
including metadata and persisted GET serialization. Public draft diagnostics contain only
bounded counts, schema paths and internal error categories. Verified claims and citations
carry approved references; draft bindings are not treated as approved references.
The existing conversation/project authorization and stored-message retention
controls apply to these sanitized diagnostics; this change adds no protected raw
debug capture or longer retention. Index activation, rebuilds and source governance
corrections require a separate authorized scope.

Regression fixtures under tests/fixtures/evaluation/post_qa_* preserve captured
source/build/hash identities. Unknown historical/rent authority labels are excluded
from the positive completion denominator. Completion, safe abstention, protocol and
latency must be reported separately; live positive seeds need three successful
repetitions before acceptance.

Canonical prompt provenance is v25 for the AnswerDraft contract and scope checks.


The finalized terminal projection supplies content, finish reason, legacy reason,
notices and failure stage together. A recovery cutoff uses recovery_deadline_exceeded;
an outer work cutoff uses request_deadline_exceeded; a separate provider timeout
uses provider_timeout. The lifecycle retains the actual 30/50/60-second boundaries.
These outcomes do not imply that the corpus lacks a rule or that an amendment is
unresolved. Request/provider timeout persistence follows the same projection as
Regular and SSE delivery. The legacy clarification funnel label remains clarification,
while terminal_outcome identifies needs_input.

Every declared governing dependency must itself have validated proof and compatible
selected scope. A shared chunk UUID is insufficient: selector ranges must belong to
the same table, category and period; duplicate UUIDs with conflicting provenance are
rejected. Cross-chunk composition requires the same document, project, source revision,
build, generation and attested table, without explicit category/period conflicts.
Questioned category membership remains an eligibility obligation in mixed calculation
or comparison tasks. Only asserted scenario premises can be treated as stipulations.
Selective citation previews include operative AGM timing clauses while the full
original supporting spans remain available.
