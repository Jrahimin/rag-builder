# Tax failure: post-deployment trace diagnosis

## Evidence examined

User-supplied response for conversation `a4018526-9e0e-433d-8eda-1d9713966e42`,
user message `fd4c167b-3581-4c30-958d-25bd8f512203`, plus live Test Lab Journey
and a focused Search against the deployed corpus.

The response uses prompt v9, project configuration revision
`439711c5-6686-46b2-9578-eafa54adf2ea`, active index
`19dd2ec8-45fc-4193-ba61-d067dc0d4ce3`, and source generation 24.
`knowledge_repair.version=v1` and `status=recovered` confirm the recovery path
ran. The trace retains five incomplete amendment records. Neither stale-index
speculation nor missing recovery deployment explains this response.

## The failing chain

### 1. Translation is enabled and attempted, but produces no usable query

The initial retrieval translation records:

```text
model = gpt-5-nano
status = failed
attempts = 2
validation_reasons = [empty, empty]
finish_reason = length
output_tokens = 256
reasoning_tokens = 256
latency_ms = 4981
```

The final reported attempt spent its entire output budget on reasoning and
returned no translation. Earlier routing changes allowed this call to happen;
they did not solve provider output budgeting.

`LLMQueryTranslationProvider._max_tokens` sizes output from query length with a
256-token default floor. Its retry loop calls the same sizing method again.
The retry therefore does not increase the budget after an empty length-limited
completion. A large configured maximum alone does not increase the short-query
allocation. Fixing this requires reasoning-aware allocation or an appropriate
translator configuration, and a bounded retry policy that responds to truncation.

Only the initial translation diagnostics are exposed in this supplied response.
The three repair branches omit their internal retrieval diagnostics because
candidate tracing is disabled. Their individual translation outcomes cannot be
claimed from this payload.

### 2. The old English Act is outside the recorded amendment targets

The answer cites the English Act document
`4687b919-ad77-4126-9a5c-2abe97e2f498`, source revision
`334f5b9a-cd9c-4f79-9676-5f76580ed6a2`.

The four Act-related amendment records instead target the Bangla Act document
`55b0a774-a020-4d4f-8a66-d9766a3b543b`, revision
`5d517f44-a172-40f7-9243-82126464ee8d`. The remaining record concerns a withholding
rules document. None of these records targets the cited English revision.

`incoming_modifiers` queries exact target revision IDs. `annotate_authority_limitations`
also matches exact base revision IDs. Neither infers legal equivalence across
separately registered translations. Consequently, excluding the unresolved
Bangla base does not exclude or qualify the equivalent old English provision.
The cited English rebate claims have `authority_status=not_assessed`.

Additionally, the existing amendment records have empty `target_provisions`
and missing modifier effective dates. Merely copying an incomplete edge to the
English document would produce uncertainty, not a usable current rule.

The corpus needs verified provision-level relationships covering both relevant
language representations. The backend source-revision contract supports multiple
relationship targets; the current simple UI does not expose the complete scope
editing workflow. Do not replace the entire Act or invent document-wide legal
dates to force this example through.

### 3. Recovery incorrectly promotes relevance into answer completeness

The recovery planner creates three queries for slabs, rebate, and employment
exemptions. It then accepts two chunks for each:

| Dependency | Selected evidence |
| --- | --- |
| Slabs | `0303a947` and `4584282c`; administrative/City Corporation references |
| Rebate | `37cdcbba` and `abd03d82`; old English section 78 and A/B definitions |
| Employment exemption | `07d2b58f` and `2d704da1`; filing amendments and employment-income definition |

`repair_knowledge_evidence` checks relevance admission, absence of a known
authority flag, and retention of at least one chunk per search after budgeting.
It does not verify that a selected span actually provides each requested rule,
including its applicable period and conditions. It then explicitly replaces the
decision with `sufficient=True` and marks `recovered`.

The contradiction is visible without relying on a guessed tax answer:
the recovery status is successful while the generated answer says the slabs
and employment exemptions are absent. This is the central false-success bug.

The planner also chooses 2026–27 without resolving that year with the user.
Search text is not an established assessment-year binding; generation still
reports the assessment year as missing. Applicability must be an explicit user
fact or clearly stated assumption, not silently introduced by a search query.

### 4. False recovery suppresses fallback and permits the old calculation

The response is `indexed_then_web`, but `web_search.status=not_requested`.
`ChatService` only requests that fallback when grounding blocks generation.
Because recovery set sufficient evidence, generation proceeds with the old
English formula. Arithmetic checks can then support 15% × 60000 = 9000 while
the rule's authority remains `not_assessed`. Correct arithmetic and citations
do not establish current legal applicability.

The response took 39,547 ms in total: retrieval/recovery 28,991 ms and answer
generation 10,525 ms. The extra recovery work did not yield a complete rule set.

## Live Journey verification

Journey confirms the ready processing state and the same active build. Its
Passed processing badge establishes job readiness, not factual tax accuracy;
Search/Chat progress is scoped to the current browser Lab session.

A fresh Bangla Search for the current section-78 amendment returned Paripatra
page 73, chunk 92 (`c74bed6d`), first with score 0.9755. Its text explicitly
describes 15% being reduced to 10%, the 10-lakh cap reduced to 7.50 lakh, and the
3% limit remaining. No expected percentages were supplied in the search query.
The current rule is present and readable in the active index.

## Why previous tests and fixes did not finish the task

The earlier fixes preserved diagnostics, blocked known unresolved revisions,
and enabled Latin-query translation routing. This trace shows those mechanisms
are insufficient when the old translated source has no matching amendment edge,
the translator returns reasoning-only output, and repair retrieves high-scoring
but incomplete evidence. Existing recovery fixtures mostly provide appropriate
rule text directly; they do not establish semantic completeness against these
real distractors. Passing those tests was not live tax-answer acceptance.

## Required coordinated correction and acceptance

1. Make translation emit validated text within a tested reasoning-aware budget;
   retain bounded failure behavior and diagnostics for each repair branch.
2. Curate authoritative amendment dates and provision targets for both Act
   representations, preserving historical applicability and unrelated provisions.
3. Require evidence-backed coverage of every calculation dependency before
   marking repair successful. Unsupported applicability must remain unresolved,
   permitting allowed fallback or a clear request for missing information.
4. Add this response shape as a regression: reasoning-only translation failure,
   separately registered English/Bangla Acts, incomplete amendment metadata,
   administrative distractors, old rule, and current amendment elsewhere in the
   corpus. A result that says rules are missing cannot pass recovery acceptance.
5. Verify a fresh live conversation with an explicit assessment year and gross
   salary, then the original ambiguous question. Judge selected evidence and
   calculation correctness, not just citation presence or Journey badges.

Further reprocessing cannot repair these runtime and source-relationship gaps.
This investigation changes documentation only; it does not claim another
runtime fix, metadata activation, or successful final tax calculation.

## Implemented follow-up (deployment pending)

The investigation above describes the deployed v1 trace. The subsequent bounded
code correction adds:

- Translation floor 1024, with length-limited retries increasing their budget up
  to the existing request/provider/2048 ceiling. Nonempty truncated output is
  rejected as well as empty output. No new environment key is required; an
  explicit low `APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS` override still applies
  to the first attempt. Remove that override or set it to 1024 for the new floor.
- Recovery v2 performs one source-only completeness review after final context
  budgeting. It checks the original question, every planned dependency, input
  transformations, and temporal/category applicability. Search-generated years
  are not user facts. Every successful check requires exact quotes bound to its
  retained candidate IDs. Missing coverage, malformed/truncated output, and
  provider failure cannot set `sufficient=true`. Existing permitted web fallback
  or insufficient-evidence behavior remains available.
- The recovery deadline is 60 seconds including planning, searches and review.
  Both LLM calls contribute to usage; the database read transaction is released
  before the review. Compact branch translation diagnostics remain available
  without candidate traces. Verification quotes are not persisted in diagnostics.
- Both metadata correction surfaces use the shared payload builder. Its `keep`
  treatment now preserves existing outgoing relationships and provision scopes
  when correcting dates/title/other metadata. Previously it submitted an empty
  list, and the backend created a new active revision without those edges.

These changes require redeployment, not document re-upload, reprocessing, index
rebuild, or a schema migration. They do not retroactively restore metadata. The
separately registered Act representations still need verified amendment targets,
effective dates and provision scopes curated through source metadata revisions.
Do not use whole-document replacement or guessed equivalence to bridge that gap.

After deployment, use a **fresh conversation**. Verify `knowledge_repair.version`
is `v2` when recovery runs, inspect branch translation diagnostics, and require
`coverage.complete=true` and `quotes_validated=true` before accepting `recovered`.
Test the explicit assessment-year/gross-salary question and the original question
without a year. Incomplete evidence must remain unresolved, not produce the old
rebate calculation. A complete answer must use the current governing passages.

The semantic review is model-based; exact quote validation prevents fabricated
citations but does not formally prove legal applicability. It protects this
recovery path, not every possible retrieval/web answer. Live accuracy acceptance
remains pending deployment and testing; deterministic mocked-provider regression
tests are not a substitute for that acceptance.
