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
  "metadata_filter": {},
  "source_scope": "project_default"
}
```

`metadata_filter` values must be strings. `document_id`, `metadata_filter`, and
`as_of` are per-request. `source_scope` is `project_default` (the compatible
default) or `indexed_only`. Omitted filters and one-turn source restrictions are
not inherited from earlier turns.

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
      "source_scope": {
        "requested": "project_default",
        "effective": "project_default",
        "origin": "project_default",
        "reason": "project_response_mode"
      },
      "verification_version": "claim-verification-v2",
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
Claim verification compares the main filing duty separately from the negative AGM
and officer fallback conditions in Bangla Companies Act section 190; those conditions
do not alone make a positive filing claim unsupported. The response fields and
verification reason vocabulary are unchanged.
Qualified officer-absence conditions outside the bounded comparison grammar retain
`unverified` status; matching negative words alone does not establish the fallback.
Authoritative coverage review accepts a quoted Bangla private-company category as
category proof. If a plan has no discovery queries, an already validated partial
checkpoint can still produce a cited partial answer with unresolved requirements
reported in existing coverage diagnostics; an unvalidated plan remains blocked.
The verifier treats explicit reviewed-passage limitations and their bounded
same-paragraph continuations as coverage claims, validating them against the
recorded gaps rather than requiring a legal citation for the absence statement.
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

Web access is suppressed whenever `document_id`, `metadata_filter`, `as_of`, or an effective
`indexed_only` source scope restricts the request. Clear corpus-only wording in English or Bangla
is resolved deterministically for that turn; negated wording does not create a restriction, and an
API `indexed_only` restriction cannot be relaxed by message text. Prior web citations are not reused
as indexed evidence.
Provider failure or empty web results fail closed. A broad compliance check first gets bounded
recovery; if that recovery reaches its deadline or completes with incomplete coverage without
validated proof, an unscoped,
non-calculation turn in a web-enabled mode may use reviewed web evidence. Scoped requests,
calculations, and current-rule applicability checks remain guarded. A local recovery deadline with
no validated proof completes as a persisted `insufficient_evidence` response; a genuine provider
failure remains a failed execution and may return 503. Diagnostics record
`knowledge_repair.fallback_route`. The web search query includes the resolved question and
reference date, with selected Project scope context when available. Search and review budget expiry
are reported as `search_timeout` and `review_timeout`. Recovery, search and review have
sequential bounded allowances; local recovery cannot consume the reserved 20-second web review
allowance. The total evidence ceiling starts after initial retrieval and adds the larger configured
recovery allowance, web-search timeout and review allowance. Search itself is capped at its
configured timeout. A search timeout is described as a time limit, not provider unavailability;
web review failures yield an insufficient-evidence response that identifies the verification failure.
If some candidate quotes are invalid but another is verified exactly, only independently
verified sources are admitted; `web_search.scope_review.status` is `reviewed_partial` while the
outer `web_search.status` is `evidence_accepted` when at least one source is admitted. An incoming MODIFIES
relationship does not by itself prove whole-provision replacement: affected base text remains
unresolved unless diagnostics contain both `provision_effect=replaces` and
`replacement_scope_verified=true`.
Indexed authority that remains unresolved after recovery also remains guarded from web fallback,
including in web-enabled response modes. Direct questions about changing facts (such as rates,
limits, thresholds, exemptions, deadlines, fees, or penalties) receive current-rule applicability
review by default and use the focused lookup allowance (15 seconds, up to two initial queries,
no follow-up); broad coverage and calculation work keeps the broad allowance. Recovery pairs the
governing category and period heading with the operative rule or table, may search source-language
terms, and does not treat a proposed table as proof. For a focused numeric rate or threshold,
partial answers require fully fulfilled central-rule and category/period proof; if either is
missing or unresolved, the value stays blocked. Separate explicit topics can retain independent
partial answers.

Turn resolution preserves the kind and years of an explicit historical period (assessment, fiscal,
financial, calendar, or named single year); the user's period outranks conflicting history or
Project defaults. Newly introduced ranges need support from the message, an active prior binding, or
domain instructions. Abbreviated ranges such as `2025-26` are parsed as one period; ISO dates are
excluded. Claim checks recognize South Asian digit grouping and English/Bangla lakh, lac, and crore
scales. Bare source numbers count as monetary evidence only when nearby text contains a
money cue; years, percentages, counts, and durations cannot be reused as amounts. These checks do
not change the request or response schema.
Broad web-only overviews in either web-enabled mode carry an explicit partial-coverage notice because
relevant cited passages do not establish that every requested obligation was found.
`source_provenance` is always one of
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


Operational note (2026-09-26): reviewed partial answers allow at most 15 seconds for optional web supplementation. `lifecycle.stages_ms.web_search` and the matching span expose its time separately. A failed supplement preserves the independently reviewed local answer and its missing coverage; it does not promote unreviewed snippets. Broad recovery balances central duties and applicability checks within the existing configurable query count. No response-schema fields were added.


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

The additive `metadata.knowledge_repair.coverage.checks` diagnostics now include
`fulfillment` (`full`, `partial`, `none`, or null for legacy records),
`unresolved_facets`, and the reviewed `answerable_scope` alongside `supported`
and exact source ranges. `full_coverage_validated` requires full fulfillment for
every required check. `metadata.knowledge_repair.answerable_scope.reviewed_scopes`
lists the established proposition, proof chunk IDs and exclusions passed to
generation for complete and partial coverage. A partially fulfilled check keeps
its cited source available while its requirement stays open for discovery and
appears in unresolved coverage gaps.
Full fulfillment is downgraded when the selected quotation does not establish a
requested period, company category, or conditional window; a nonempty
`unresolved_facets` list cannot certify complete coverage. Input-only gap review
is reserved for checks with fully resolved source obligations.

`metadata.knowledge_repair.discovery_query_normalization` records each original
query, executable query, reason, and owning requirement IDs. `review_inputs`
records supplied chunk/evidence-unit IDs and branch candidates; any exhausted
identity correction appears in `review_protocol_errors` with affected IDs.
These fields are diagnostics in existing metadata, so the public message schema
and stored citation coordinates are unchanged. Test Lab displays reconstructed
document envelopes separately from evidence-unit character ranges and reports
unknown evidence length when local offsets are unavailable.


### Temporal evidence and task-scoped recovery (2026-09-29)

Internal claim and evidence checks share typed quantities with exact source spans
and decimal values. The representation classifies money, rates, periods, locators,
counts and durations; it does not itself prove that a number applies to a requested
claim. Authoritative recovery records task kind and per-requirement dependencies so
partial answers can retain independently supported topics while preserving each
claim's own applicability requirements. Recovery may request bounded adjacent
context when ingestion metadata says a heading or table exceeded the context
budget. These are internal verification behaviors; no request or response fields
were added. Regression coverage for grouped-decimal arithmetic, wrong-result
rejection, and table-overflow adjacency now passes. The follow-up type corrections
also pass the full backend unit and architecture suite, Ruff format/lint checks, and
mypy. Focused live QA used the active Income Tax corpus; these internal checks
do not establish a performance target.

Focused recovery uses canonical requirement IDs internally for missing-rule
queries; unknown and cyclic dependencies are rejected. The existing
`metadata.knowledge_repair.stop_reason` and
`metadata.knowledge_repair.requirement_progress.stop_reason` values expose
`recovery_deadline_exceeded` to clients, which Test Lab labels as a timed-out
source review rather than a confirmed lack of evidence. The response schema is
unchanged.

For a numeric-rule partial answer, the proof handoff retains only fully supported
requirements and their governing dependencies. The generated response should
state only that reviewed scope and describe unresolved topics as coverage gaps.
Clients should continue to use `grounded` and the factual claim counts in
`metadata.evidence_summary` to distinguish a supported cited answer from a
generated answer with unsupported details. No response fields were added.
For reviewed partial answers, unsupported or unverified paragraphs are removed
before final persistence and grounding is recalculated. If no supported answer
remains, the answer is withheld with no citations. The existing metadata may
include `verification_repair.status` as `pruned_unverified_paragraphs` or
`withheld_unverified_partial_answer`; streamed partial responses emit their
answer text only after this verification step.

All streamed factual answers are held until claim verification finishes. If
the only failures are missing citation markers, the server checks each uncited
claim against selected passages, adds markers only for supported claims, then
runs strict verification again. An answer that remains ungrounded is withheld
unless a replacement assembled from fully reviewed requirement scopes passes
the same strict claim check. Its repair status is
`verified_coverage_scope_fallback`. Otherwise the response is withheld
with `insufficient_evidence_reason: "claim_verification_failed"` and no
citations. `metadata.verification_repair.status` can be
`repaired_missing_citations` or `withheld_unverified_answer`. This reason means
the generated wording failed verification; it does not establish that the
corpus lacks a relevant source.


## Additive evidence diagnostics

Existing v1 envelopes remain unchanged. CitationSnapshot adds optional supporting_spans, structural_context and provenance_precision; AnswerClaim adds requirement_ids. Message metadata adds normalized_scope and answer_draft. supporting_spans carry reviewed requirement IDs and exact quotations. Character locations remain null when reconstructed content lacks a reliable contiguous offset mapping. POST, buffered SSE final and persisted GET use the same verified stored assistant message.

Validated private-build replay: optional preview_index_build_id on message POST/SSE selects only a sealed validated build belonging to this Project. It does not activate the build or change the active/previous pointers. Invalid, cross-Project and unsealed builds are rejected. Recovery pins the same build and generation.

Request scope distinguishes legal assessment/fiscal periods from instrument title years. Known-at dates carry an inclusive flag: “known before” excludes publication on the cutoff date; “available on” includes that date. A deadline terminal retains request-local preview/build/source generation/scope and measured latencies for POST, SSE and persisted GET diagnostics. request_deadline_exceeded identifies the outer work cutoff, recovery_deadline_exceeded identifies the recovery cutoff, and provider_timeout identifies a separate provider timeout. The selected cause also supplies finish_reason, notices and terminal_outcome.failure_stage.


### Additive terminal answer outcome

Regular message POST, persisted message GET and SSE done use the same optional
terminal_outcome object. Existing content, claims, citations, grounded,
insufficient_evidence_reason and response envelope stay compatible. Older messages
may have terminal_outcome=null.

Example: {"version":"answer.outcome.v1","outcome":"verification_failed",
"reason_code":"answer_draft_invalid","failure_stage":"draft_schema",
"requested_scope":{"requested_periods":[]},"coverage":"complete","retryable":true,
"next_action":"retry","supported_requirement_ids":["R1","R2"],
"unresolved_requirement_ids":[]}.

outcome: answered | partial | needs_input | insufficient_evidence |
unresolved_authority | verification_failed | timed_out.
failure_stage: retrieval | coverage | draft_schema | claim_verification | persistence
or null. next_action: retry | supply_input | review_source | contact_operator | none.
A successful HTTP response can carry a failed answer outcome. Schema/verifier
protocol failures differ from genuine absence of selected evidence. For a structured
draft with a mix of valid and invalid proof bindings, valid segments are verified and
published independently; rejected segments remain excluded and unresolved required
parts produce the ordinary partial outcome. If no segment is independently provable,
the answer is withheld. A response that ends with a detectable cut-off fragment is
also withheld rather than marked answered. Public supported claims never include a
rejected factual draft.

Message metadata.lifecycle uses the same timing/action schema on normal and deadline
paths. persistence_completed is separate from whether that snapshot includes
persistence timing. SSE done additionally includes terminal_outcome and lifecycle;
the persisted MessageResponse remains the source of durable content/claims/citations.
Only sanitized bounded schema paths, error categories and candidate counts are
retained for operator diagnosis; raw failed assertions and secrets are excluded.


Public metadata.answer_draft is a bounded diagnostic summary, not the AnswerDraft
generation payload. It never includes segments or their text; this also applies
when serializing earlier stored messages that contain draft payloads. No stored
row is rewritten by GET. Verified claims and citations provide approved references.
The existing non-null metadata dictionary contract is unchanged.

The typed insufficient_evidence_reason enum adds recovery_deadline_exceeded and
provider_timeout. timed_out notices use recovery_timeout, request_timeout or
provider_timeout consistently with the actual cutoff. A deterministic nonfactual
terminal skips semantic verification; any factual partial claim retains full
verification. Language resolution supplies the same EN/BN content to Regular,
SSE and persisted GET.


### Operator message diagnostics

`GET /api/v1/projects/{project_id}/conversations/{conversation_id}/messages/{message_id}/diagnostic`

Requires an operator session and Project access. The message must belong to the
requested Project and conversation. Reads are audited. Returns bounded summary
metadata and an optional full payload. Full payloads expire after seven days and
are returned as `null` when expired; summaries remain available.

```json
{"success":true,"data":{"message_id":"00000000-0000-0000-0000-000000000001","project_id":"00000000-0000-0000-0000-000000000002","summary":{"version":"message.diagnostic.v1"},"payload":null,"expires_at":null,"expired":false}}
```

For opt-in QA, send `X-APE-Diagnostic-Capture: full` on the existing Regular or SSE
message POST. This requires operator authorization and the existing CSRF policy.
Organization API keys cannot request capture. The public Message contract remains
compatible: `claims[].assertion_id` is optional and `metadata.execution` is additive.
Full diagnostic payloads and rejected assertion text do not appear in public
Regular/SSE/GET responses. Attempted and published verification counts appear
separately in message metadata.


Full diagnostic artifacts may include original/source and local/admitted offsets, optional
requirement origins and assigned scopes, partial limitations, and completed/interrupted attempts.
These private records remain subject to operator/project access and seven-day payload expiry;
public responses contain only the execution summary and verified claims/citations. The finer
terminal cause does not remove the existing insufficient_evidence_reason compatibility field.


## Phase 2 assertion and execution contract

Verified public `claims` retain `assertion_id`, `requirement_ids`, evidence references,
`calculation_references`, and structured `verifier_failures` with an assertion ID,
failed dimension and bound source IDs. Failure dimensions distinguish subject/category,
period, condition, certificate, quantity role, scope and legal effect. Rejected text
and calculation graphs are exposed only through authorized, opt-in full operator
diagnostics; the existing project/conversation/message authorization and seven-day
expiry apply. Typed `notices` distinguish documentary scope, proposal scope and
unresolved applicability from factual claims. Regular Message responses, SSE `done`,
production evaluation and persisted GET share finalization.

`metadata.lifecycle.deadline` reports the frozen execution policy, budget class,
hard request duration, p95 target, promotion requirement, stage estimates and
completed stages. `metadata.lifecycle.counts` reports shared malformed-output and
semantic repair credits. `known_corpus_gap` is a completed, non-retryable limitation
with `next_action: review_source`; provider/deadline failures remain distinct.

An explicit new immutable Project AI revision can set
`execution.execution_policy` to `adaptive_v1`; `legacy` is the default for old and
unspecified revisions/snapshots. Refreshing a conversation appends a new snapshot
through the existing config endpoint. It never changes historical behavior.
Adaptive limits are 45 seconds simple or 120 seconds complex (p95 targets 30/90),
with at most one dependency-based promotion from the original start. Recovery
allowances are two initial recovery searches, three/six total recovery searches,
and two independent structural completions. This contract does not activate a
revision or index automatically.

When facts are withheld, public claims remain empty and factual citation
coverage is not_applicable (numeric compatibility value zero). Attempted and
rejected assertion counts remain in execution; protected diagnostics preserve
rejected IDs, proof bindings and failure dimensions. rejected_draft.failed_count
counts assertions, while failure_reason_count counts distinct reason categories.
Evaluation stores rejected diagnostics separately from its accepted claims.

Evaluation's execution projection includes typed notices and the frozen
lifecycle estimate contract. Message responses, SSE done and persisted GET
retain their ordinary top-level notices and verified claims. A committed Message
is published once even when a supplementary timing metadata write fails or
crosses the ceiling. Such a failure can omit the completed measurement from
durable metadata; it cannot replace the accepted content with a timeout.

### Blocker extension contract clarifications

Ordinary and typed rejected assertions retain stable assertion IDs in protected
operator diagnostics. Successful published claims exclude rejected drafts.
Deadline terminal responses with no published facts use grounded=false and empty
claims/citations. Metadata preserves the completed response_policy and web_search
decision when preparation completed before the deadline.

Regular, SSE, evaluation, and persisted GET share the exact message.execution.v1
projection. Evaluation timing and notices are carried separately from execution;
elapsed timing is not part of that deterministic projection.

Verification protocol failure remains verification_failed with no published
claims or citations; deterministic semantic repair cannot mask a malformed or
unavailable verifier. A source-policy suppression status such as
suppressed_unresolved_authority remains in web_search metadata even when the
request deadline prevents provider work. Persisted deadline responses carry the
runner's grounded=false value through Regular, SSE, and GET serialization.


Optional Message fields: `provider_provenance` contains sanitized effective stage
provider/model/reasoning/purpose/schema and endpoint/capability identities;
`citation_coverage_status` is applicable or not_applicable. Old stored Messages
without these fields remain readable (empty provenance/null status). No private
operator diagnostic payload is exposed. Verified documentary statements retain
their citations with typed applicability limitations.


Published partial outcomes expose exact supported_requirement_ids and
unresolved_requirement_ids plus unfulfilled_requirements notices. Source sufficiency
does not imply all requested parts were published. Lifecycle separates acknowledged
answer_persisted from complete_turn_measurement_available; only the completed
measurement is eligible for acceptance latency gates.

### Publication and failure correlation

Regular responses, SSE final events, and saved GET messages share the publication
contract: unsupported siblings are withheld while independently verified claims
can publish as `partial`. Citation indexes are compacted to the used records, and
claim evidence carries the same updated indexes. Source gaps appear in concise
notices; operator diagnostics retain the full requirement inventory.
A provider token limit or visibly incomplete trailing clause cannot publish as
`answered/complete`. Complete coverage rejects damaged source conditions; it
cannot certify source applicability without proof.

Unexpected HTTP failures carry the same `X-Request-ID` header and
`error.request_id` body value for correlation. A captured generic 500 body does
not itself identify the underlying exception or its cause.

Completeness applies to each assertion before rendering; complete siblings can remain
`partial` when a different assertion is unfinished or its completeness is unknown.
The optional `publication_complete` field belongs to the internal provider-verifier
wire contract, not the public Message schema. The public schema is unchanged.
For reconstructed source text, unattested page/character locators are returned as
null and citation `provenance_precision` is `unknown_source_locator`. Distinct
attested locations are retained in supporting span records instead of being merged
into a misleading contiguous locator. User notices retain all independent material
proof gaps while removing repeated descriptions and internal requirement labels.

For newly published messages, `[n]`, `claims[].evidence[].citation_index` and
`citations[n-1]` refer to the same source after compaction. Chunk/document identities,
indexed hashes and claim proof hashes survive projection; selected context counts
and published cited counts remain distinct. GET serializes this saved result and
does not renumber older persisted snapshots. Gap descriptions retain their terminal
punctuation; repeated labels or punctuation variants do not duplicate a material gap.
Provider output receives the same source and completeness checks regardless of its
provider label; unverified or unfinished output persists `verification_failed`.
