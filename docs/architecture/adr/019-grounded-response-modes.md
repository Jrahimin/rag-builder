# ADR-019: Grounded chat response modes and web-search boundary

**Status:** Accepted  
**Date:** 2026-08-25

## Context

Strict indexed RAG must remain the default, while selected Projects need current public-web
evidence. Web access must not become an LLM-selected escape hatch or leak vendor APIs into chat
orchestration. Contextual generation has a different trust boundary: its caller supplies the
authoritative context and often requires deterministic structured output.

## Decision

- Add sparse Project chat policy `response_mode`, inheriting the deployment default
  `indexed_only`.
- `ChatService` chooses the workflow before prompt construction:
  `indexed_only`, `indexed_then_web`, or `indexed_and_web`.
- The existing knowledge evidence gate remains unchanged. `indexed_then_web` searches externally
  only after that gate says Project evidence is insufficient. Document-, metadata-, and
  time-scoped requests never fall back to the public web.
- External search is a `BaseWebSearchProvider` port. The first implementation uses OpenAI
  Responses API `web_search` with live access and forced tool execution; vendor payloads remain in
  `platform/providers/implementations/`.
- Web evidence is admitted only when a provider associates a cited URL with source-returned
  excerpt text and a lightweight relevance check accepts it. Model-generated search summaries are
  never promoted to source content; unclear associations fail closed.
- Retrieved page text is untrusted evidence. Search extraction and the final v5 prompt both reject
  instructions found in web content. Generation never fills missing evidence from model memory.
- Messages and SSE terminal events persist `source_provenance` as `knowledge`, `web`,
  `knowledge_and_web`, or `none`, plus provider/status diagnostics and source-specific citations.
- `/generations` remains caller-context-only. Neither web-provider readiness nor source-aware chat
  prompt constraints govern that workload. Web enrichment is disabled and the trace explicitly
  records `context_authority=caller_context`, `web_enrichment_used=false`, and
  `source_provenance=none` without exposing chat response policy.

## Consequences

- Existing Projects and historical snapshots remain `indexed_only` by default.
- Web provider failure or empty results fail closed; they never trigger parametric fallback.
- Runtime web-provider composition is non-fatal: knowledge-sufficient turns remain available, and
  only turns that need web evidence return the fail-closed no-answer.
- Combined mode can expose conflicts because knowledge and web blocks/citations stay distinct.
- Enabling a web response mode requires configured credentials and the source-aware v5 prompt.

## Amendment: one admission path (2026-09-03)

Grounded chat now has one reranked admission path (`assess_candidate_wise`) and one no-reranker
fallback that includes the cross-language semantic bar. `observe` uses the same selected admitted
units as `enforce` and disables only the generation veto. `balanced` remains a two-band structure
whose high band stays disabled until a hard-negative calibration run shows a positive margin below
the identity's high reranker bar. Deployment default stays `strict`. This amendment does not
change response-mode or web-search boundaries.
