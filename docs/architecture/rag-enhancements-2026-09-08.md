# RAG evidence approaches and execution improvements

This change adds project-owned evidence behavior, reduces duplicate retrieval work, and makes the answer lifecycle visible. Existing projects retain authoritative behavior. New projects receive an ordinary immutable configuration revision with factual behavior; operators can select factual, authoritative, or multi-perspective in Project AI settings.

## Runtime behavior

- Factual questions use ordinary admission and direct generation when sufficient. Comparison cues use the existing structured evidence exchange when needed; there is no unconditional new classifier.
- Authoritative answers retain the proven v15 planning/coverage protocol with amendment, applicability, temporal, and rule-dependency checks. This compatibility decision follows repeated gross-salary regressions with semantic requirements. It is explicitly recorded as `authoritative_compatibility`; stable semantic requirements and initial-proof short-circuiting are not rolled out to authoritative Projects. Exact source line ranges remain validated and saved. Retrieval/cache/concurrency improvements still apply.
- Factual and multi-perspective reviews use stable semantic requirement identifiers instead of search-route positions. Exact proof survives focused recovery and is prioritized before new search hits. Initial evidence can satisfy the request without recovery searches. Compact request-local source labels avoid asking the model to copy UUIDs; structured source-line records place original numeric selectors beside the text. Blank presentation lines are omitted without renumbering the original source. Saved proof resolves back to exact chunk identities and original inclusive ranges. A missing or blank selected range receives one bounded structural correction attempt, then fails closed if still invalid. Source text remains untrusted.
- Multi-perspective context selection prioritizes explicitly requested works and distributes space across admitted works before adding repeated passages. Disagreement is attributed rather than resolved by recency, primary-source status, or passage count alone.
- Optional immutable `work_key` metadata groups reprints/translations. Missing keys fall back to source groups. Exact copies are joined transitively for reviewed-work counts. A reviewed work is not a claim of independent authorship.
- First messages normally skip conversation resolution. Explicit historical language can require date interpretation, preserving literal date bindings and source cutoffs. Ambiguous periods can require clarification; year-only labels do not silently become invented calendar dates.
- Unsupported arithmetic remains unverified even when surrounding prose resembles source text. User inputs and explicit scenario assumptions have separate provenance; source coverage does not prove personal circumstances or every generated claim.

## Work reuse and concurrency

`RequestWork` lives for one turn only. Exact embedding keys include project, provider, model, provider version, dimensions, purpose, and text hash. Query/document vectors stay separate; mismatched, failed, or cancelled results never fill the cache. Content reuse is scoped to the same project and immutable chunk identity. There is no answer cache.

Whole-chunk reranking precedes passage embedding/scoring. Provider failure retains the existing bounded fallback candidate set. Recovery runs at most three branches per turn, each with its own database session, pinned index, source generation, reference date, configuration and filters. Results merge in planned order; failures and cancellations propagate. A process bound and a Redis deployment lease bound constrain concurrent recovery to twelve branches. The 180-second lease exceeds the enclosing 120-second recovery deadline. Redis 3 compatibility requires reading server time before the atomic Lua admission script; no new service or Docker deployment is needed.

Context ceilings remain ceilings. Complete admitted evidence units are preserved; coverage is not manufactured by trimming away exceptions or conditions. Prompt preflight reserves output and framing, uses a recognized model tokenizer when available, and otherwise reports a conservative UTF-8 byte upper bound. Actual generation usage is recorded separately. Deployment mappings can restrict model capacities. The hosted Luna default uses its documented 1,050,000-token capacity, verified on 2026-09-08: [official model specification](https://developers.openai.com/api/docs/models/gpt-5.6-luna). This does not imply that the installed tokenizer recognizes that model alias.

## Observability and console

Versioned lifecycle metadata records snapshot/history loading, resolution, translation, embedding, database retrieval, relationships, content loading, passage scoring, reranking, coverage/recovery, generation, and claim verification. Parallel stage durations can overlap and must not be summed as wall time. Provider records contain attempt counts, duration, available usage, and explicit unknown values for hidden provider-internal retries. Aggregate metrics remain available when detailed candidate tracing is disabled.

Assistant processing totals include claim verification. Persistence and full HTTP/SSE completion are correlated request-log events because persisted assistant metadata is captured before its own persistence. SSE emits coarse searching/checking progress while preparation runs, then preserves answer streaming. Progress does not bypass evidence checks or establish faster total execution.

The console shows saved conversation settings beside the current Project revision, evidence counts, coverage versus claim verification, token budgets, and lifecycle/provider work. Ordinary factual admission reports coverage `not_assessed`, because relevance alone does not establish that the requested fact exists. Historical conversations retain their saved policy. Source upload and metadata correction expose the optional work key.

## Local rollout and rollback

Apply additive migration `20260908_0033` after `0032_rag_phase2_grounding`; it adds nullable source revision `work_key`. It has been applied to the local database. Existing documents need no blanket reprocessing. Restart the ordinary local API/workers to load runtime changes; frontend development uses Vite.

The existing local tax Project retains authoritative behavior and translation disabled. The context experiment restored the original revision 11 policy through ordinary immutable revision 17, including the original 24-chunk ceiling. The live console was inspected without deployment or configuration mutation. Runtime rollout to live remains separate from these local changes. Project policy rollback uses normal immutable revisions; historical conversation snapshots continue to identify their actual settings.

## Reproducible validation

`scripts/evaluate_rag_enhancements.py` replays fixed synthetic tax questions against an existing local corpus using production composition. `--backend` selects an archived checkout for baseline comparison. `--repeat 3` creates separate conversations. Optional `--context-ceilings 4 8 12 16 24` creates ordinary configuration revisions and restores the original policy through compare-and-set; it will not overwrite a concurrent operator change. Source corpus and active index are unchanged. `--as-of` provides an explicit timezone-aware historical source cutoff.

`python -m app.cli rag-journey --fixture ../tests/fixtures/journeys/evidence_approaches_v1/journey.json --repeat 3 --set behavior.evidence_approach=factual --compare behavior.evidence_approach=multi_perspective --keep-project` exercises fictional film facts, conflicting records, missing facts, competing accounts, reprints and single-source scope. Run from `backend` using its virtual environment. It creates an isolated local evaluation Project and retains it for inspection.

`scripts/summarize_rag_enhancements.py` summarizes saved runs without provider calls. Refusals, clarifications, and errors are distinct from answers. First/subsequent cohorts are not verified cold/warm caches; no provider or database cache eviction is claimed. Small-sample p95 is descriptive only. Quality-preserving latency claims require answer and evidence review, not just a lower elapsed time.

Measured results, failed gates and rollout limits are recorded in the [validation report](./rag-enhancements-validation-2026-09-08.md).
