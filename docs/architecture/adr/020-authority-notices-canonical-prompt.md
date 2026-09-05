# ADR-020: Authority redaction, structured notices, and canonical grounding prompt

## Status

Accepted

## Context

Phase 1 and Phase 2 of the RAG journey quality review left three generation-side
problems:

1. Provision redaction ran **after** admission, so superseded base text could
   still be assessed and then stripped, requiring post-admission reconciliation
   (`AUTHORITY_CONTEXT_EMPTY`).
2. Hard document scope plus an excluded effective MODIFIES record refused
   generation, but only when the English token `current` appeared in the query.
3. Chat selected among prompt templates v1–v5 at runtime, including a v5 gate
   for web response modes.

Notices must stay system metadata. They are not evidence, not citations, and
not LLM text.

## Decision

**Authority.** Chat redacts superseded provisions **before** admission, using
the explicit `modifies_expansion_records` list from retrieval diagnostics.
A fully redacted chunk is absent from the candidate set. If the modifier
revision is not among retrieved chunks, redaction is skipped. Hybrid retrieval
keeps status/counts on candidates but stamps the record **list** only on the
first hit so SearchService can populate top-level diagnostics; later hits do
not carry the list.

**Notices.** Assistant messages expose `notices: [{kind, language, text, source}]`.
Kinds are `scope_excludes_effective_modifier`, `web_evidence_used`, and
`insufficient_evidence`. EN/BN text comes from a small registry. Hard scope
that excludes an effective modifier **answers from admitted scoped evidence**
and attaches the scope notice. Web search remains suppressed for scoped
requests. Web-fallback sentences are no longer prepended to `content`.

**Prompt.** One canonical grounding template (`GROUNDED_PROMPT_VERSION`, currently
`v8`). Conversation create/update no longer accept `system_prompt_version`.
Assistant messages and evaluation snapshots stamp that constant. Existing v1–v5
rows still run the canonical prompt; DB columns remain provenance.
Web modes no longer depend on a prompt-version string.

**Claims.** Polarity-only answers (`Yes.` / `No.` / `হ্যাঁ` / `না`) with admitted
evidence set `grounded=null` and `evidence_gate.claims_status=no_verifiable_claims`.
Polarity without admitted evidence still refuses before generation.

**Follow-up retrieval.** Conversations run one bounded turn-resolution step
before retrieval. The effective query may differ from the raw latest user
message; `document_id`, `metadata_filter`, and request `as_of` remain the current
request and are never taken from a previous turn. The original user message
stays the generation user turn. Evidence Quality still uses `previous_user_query`
as a single-turn measurement hint only. The extra concatenated follow-up query
variant is still not implemented.

**Embeddings.** HybridRetriever reuses the original query vector for modifier
dense branches. Passage rescue still embeds separately. Vectors are not passed
across chat ↔ retrieval.

## Consequences

- Scoped “current” questions can present historical scoped values; integrators
  must render `notices` so users do not treat them as the latest amendment.
- Prompt wording applies to every Project; answer style may shift (partial
  answers, calculations, yes/no, effective dates).
- Search API hits after the first no longer duplicate expansion record lists.

## Alternatives considered

- Keep post-admission reconciliation: rejected; it existed only to paper over
  redaction-after-admit.
- Generic notice framework: rejected; three purpose-specific kinds are enough.
- LLM query rewriting for follow-ups: rejected as a general rewriter. Bounded
  turn resolution now produces an effective retrieval query from validated
  interpretation; it is not concatenated query rewriting and does not change
  retrieval algorithms.
