# Test RAG journey

Local production-path regression for factual RAG conversations. Shipped fixture
packs are `tax_v1` (six synthetic tax sources, 21 standalone turns, continuity
sequences) and `business_conversation_v1` (expense-policy/amendment plus
standard/premium support, standalone turns, and the same six sequence
intentions on a second domain). Each run creates a temporary Project and
exercises the retrieval, authority, grounding, citation, scope, refusal,
multilingual, and bounded turn-resolution path used by the product API.

It is not an evaluation dataset and does not replace Evidence Quality runs. It
exists to catch regressions on a known bilingual authority graph. The harness
may `--set` or `--compare` query-time Project AI leaves; it must not lower
evidence thresholds, weaken citations, or rewrite fixtures so a local run can
report 21/21.

## Architecture

```text
python -m app.cli rag-journey [--fixture PATH] [--replay-raw-retrieval]
        │
        ├── force APE_JOBS__BACKEND=inline and
        │         APE_JOBS__DISPATCHER_ENABLED=false (this process only)
        ├── preflight: loopback DB/storage, default Organization
        ├── Project + sparse Project AI revision
        ├── ingest 6 sources (MODIFIES + provision scopes)
        ├── immutable index build + activate
        ├── phrase-anchor → chunk mapping
        ├── baseline cases via ChatService
        ├── optional --compare-translation (same index; translation_off)
        ├── optional one-factor --compare (same corpus fingerprint)
        ├── artifacts/rag-journey/tax_v1/<run-id>/
        └── relationship-aware purge (unless --keep-project)
```

| Component | Role |
| --------- | ---- |
| `backend/app/cli/rag_journey_cli.py` | Argument parsing, inline job override, exit codes |
| `backend/app/cli/rag_journey.py` | Manifest, orchestration, assertions, reports, cleanup |
| `backend/app/modules/conversations/turn_resolution.py` | Contracts, JSON parsing, history bounding, and deterministic binding/date validation |
| `backend/app/modules/conversations/turn_resolver.py` | One production resolution call through the conversation LLM |
| `backend/app/modules/conversations/prompts/turn_resolution.py` | Versioned resolver prompt |
| `tests/fixtures/journeys/tax_v1/journey.json` | Sources, phrase anchors, 21 standalone cases, continuity sequences, fixed `reference_time` |
| `tests/fixtures/journeys/business_conversation_v1/journey.json` | Second-domain expense/support corpus and the same six sequence intentions |
| Knowledge + retrieval workflows | Upload → parse → chunk → embed → index, including per-document language inventory on the build manifest |
| `ChatService` / `GroundingService` / `ContextBuilder` / `current_authority` | Production message path the cases assert against |

The runner is a thin operator tool. It does not implement a second RAG stack.
Cases call the same conversation send-message path, including `as_of`, hard
`document_id` scope, query-language routing, and retrieval diagnostics. The
journey enables `store_candidate_trace` on that ChatService only so recall and
admission can be scored from `retrieval_selected` / `context_selected`. Production
chat stays compact (`APE_CHAT__STORE_CANDIDATE_TRACE` defaults to `false`).

## Data flow

```text
preflight
  → create Project on the default Organization
  → activate baseline ProjectAIConfig (--set leaves only)
  → ingest corpus with source metadata / MODIFIES
  → wait for active vector + lexical index
  → map evidence anchors by unique phrases (not chunk UUIDs)
  → for each standalone case: create conversation → send query → evaluate stages
  → for each sequence: one conversation, ordered turns, fresh ChatService per turn
  → optional --compare-translation: one new AI revision (`query_translation_enabled=false`), same index
  → optional --compare: one new AI revision, same index, second variant
  → optional `--replay-raw-retrieval`: after each production turn, search again
    with the raw query and original request filters; never generate from replay
  → write results.json + summary.md
  → purge modifiers before targets, then delete the Project
```

Jobs in the CLI process use inline durable-job transport even when the
deployment is configured for Taskiq. The dispatcher is also disabled so outbox
polling cannot race the in-process handler. Both overrides are process-local
and restored on exit.

## Corpus and authority graph

Fixture files live under `tests/fixtures/journeys/tax_v1/corpus/`. A
representative hosted run indexes 6 documents into about 25 markdown/semantic
chunks.

| Source key | File | Role |
| ---------- | ---- | ---- |
| `tax_2023` | English 2023 Act | Base statute; markdown heading chunks |
| `tax_2023_bn` | Bangla 2023 Act | Parallel translation of the 2023 Act, not a modifier |
| `tax_rules_2024_bn` | Bangla 2024 Rules | Complementary procedure; no `MODIFIES` edge |
| `tax_guidance_2025` | Mixed-language 2025 guidance | Procedural only. Language detection is `mixed`, so ingestion uses **semantic chunking**, not Markdown heading splits. Must not override 2023/2024/2026/2027 substantive authority |
| `finance_2026` | Finance Act 2026 | Modifies Sections 10, 21, and 40 of both 2023 language sources |
| `finance_2027` | Finance Amendment 2027 | Effective 2027-07-01; modifies the 2026 rebate/example/authority provisions. The 2026 threshold and unchanged source-tax rule remain effective |

Authority chain:

```text
2023 Act (EN/BN) → Finance 2026 → Finance 2027
```

The 2025 mixed-language guidance sits beside that chain as a procedural source only.

Authority is provision-scoped. A newer document does not replace an entire older
document. Parallel EN/BN 2023 texts are translations, not conflicting
amendments. `finance_2027` does not modify the 2026 tax-free threshold or the
savings-certificate source-tax provision, and the 2027 fixture **does not
restate** `BDT 400,000`. Composed 2027 rebate + threshold answers must therefore
retrieve both the 2027 rebate provision and the still-effective 2026 threshold.

Mixed-document journey anchors resolve by unique content phrases such as
`VR-2025-APE` and `14 calendar days`. They do not depend on Markdown headings or
exact semantic chunk boundaries.

Index builds record per-document `document_language` / chunk language counts.
Hard-scoped retrieval uses that inventory so same-language documents do not
spend a translation call.

## Assertion model

Case definitions are in `tests/fixtures/journeys/tax_v1/journey.json`.

| Field | Behavior |
| ----- | -------- |
| `expected_tokens` | Required normalized facts, including Unicode/Bangla digits |
| `expected_token_groups` | AND across groups, OR within a group. Accepts semantically equivalent wording such as `savings certificates` vs `approved savings certificates` |
| `expected_any` | At least one equivalent wording (discourse / unchanged-rule markers) |
| `user_parameter_tokens` | Values supplied in the user query (for example BDT 75,000). Must appear in the answer; they do not require a knowledge-base citation by themselves. Rules and rates used to calculate from that parameter still need retrieved evidence |
| `required_anchor_groups` | OR within a group, AND across groups. Each group must be retrieved, admitted, and used by grounded claim evidence |
| `content_match_anchors` | Phrase-only mixed-document groups. A same-source chunk that contains the phrases, or an immediate neighbor of one that does, counts. Does not pin a runtime chunk UUID |
| `prohibited_final_sources` | Rejects listed documents from admitted/cited/claim evidence only |
| `prohibited_answer_tokens` | User-supplied amounts must not be replaced by fixture examples |
| `correction` | Stale-claim cases must state the new facts plus a correction marker; repeating the old tokens is not required |
| `mode` | `answerable` (default), `scope_isolation`, `no_answer`, or `clarification` |
| `document_scope` / `as_of` / `metadata_filter` | Production hard scope, effective date, and per-request metadata filter. Omitted filters are not inherited from earlier sequence turns |
| `expected_resolution` | Optional structured check of outcome, relation, active values/origins, absent binding kinds, prior-turn references, temporal intent, and effective snapshot. Does not assert rewritten question prose |

Failure stages localize where the production path broke: `turn_resolution`,
`execution`, `retrieval`,
`admission_grounding`, `context_selection`, `citation`, `claim_grounding`,
`generation_refusal`, `authority`, `fallback`. `admitted_count > 0` with
`context_selected_count == 0` is `context_selection`
(`authority_context_empty` or `context_selection_empty`), not
`admission_grounding`. A generated but ungrounded answerable turn is
`claim_grounding`; `generation_refusal` is a pre-generation refusal or a
missing expected fact. Clarification turns do not retrieve, search the web, or
generate factual answers; they are excluded from answerable recall, refusal,
citation-coverage, and groundedness denominators. An unexpected clarification
fails an answerable turn.

Context budgeting follows production `ContextBuilder` rules: admitted
`EvidenceUnit`s are omitted when they do not fit `context_char_budget`, never
truncated. Selected units keep reranker relevance when provenance dropped the
field, so later claim checks still see the applied rerank score.

Reports split **correctness**, **provider degradation**, and **latency**. A
reranker timeout or rate limit is `rerank_status=unavailable` with a sanitized
`failure_reason`; it is not a semantic RAG failure.

Harness-only details that must stay in the fixture, not in production thresholds:

- **Historical 15% (`historical_rebate_rate`).** One valid source is enough: 2023 Act EN, 2023 Act BN, the 2024 Rules clarification that the 15% rate was unchanged on 1 January 2024 (`historical_rebate_rate_2024_bn`), or the 2026 sentence that the previous 15% rate remains relevant only for historical questions. `finance_2027` stays prohibited for a 2024 `as_of`. Older sources are not globally banned because unchanged provisions can remain valid.
- **Historical bilingual (`historical_rebate_bilingual`).** Any of 2023 EN, 2023 BN, or the 2024 Rules historical restatement is enough. `finance_2026` and `finance_2027` stay prohibited as final sources.
- **`current_2027_rebate_and_threshold`.** Requires 2027 evidence for the rebate **and** 2026 evidence for the still-effective threshold.
- **Mixed-document cases.** Phrase-only 2025 guidance anchors. `mixed_document_bangla_retrieval` sets `content_match_anchors` so grounded/cited evidence from `tax_guidance_2025` whose content contains `VR-2025-APE` (or an adjacent semantic chunk) counts. The code-switched case still uses exact mapped IDs and requires production `query_language_profile` translation diagnostics; it does not require a rewrite to be applied.
- **Declared 75,000 (`declared_investment_75000`).** The amount is a user-supplied parameter. The case requires the 2026 rebate-rate evidence and the calculated 10% / 7,500 result; it does not require citing the 2024 Rules example amount merely to prove the input. Prohibited tokens still catch substitution of the fixture's 60,000 example.
- **Hard-scoped current queries.** Answers from admitted scoped evidence and must attach
  `notices.kind=scope_excludes_effective_modifier` when MODIFIES expansion reports
  `suppressed_document_scope` with an effective modifier (`effective_modifier_excluded_by_scope`).

All factual claims remain independently verified. `grounded` still means every
factual claim is supported. Citation and provenance requirements are not
relaxed.

## Case coverage

The manifest keeps the original 10 cases and adds bilingual, mixed-source,
temporal, user-amount, and mixed-document coverage. Total: **21**.

| Intent | Cases |
| ------ | ----- |
| Scoped facts | `eligible_investments_scoped` |
| Current 2026 rebate / threshold / source tax | `current_rebate_calculation`, `current_threshold`, `unchanged_source_tax` |
| Historical 15% | `historical_rebate_rate`, `historical_rebate_bn_scoped`, `historical_rebate_bilingual` |
| Bangla / Banglish current rebate | `current_rebate_bangla`, `current_rebate_banglish` |
| Stale 15% correction | `stale_rebate_correction` |
| Mixed 2026 rate + 2024 Rules evidence / declared amount | `current_rebate_with_savings_evidence_bn`, `declared_investment_75000` |
| 2027 chain | `current_rebate_2027`, `rebate_as_of_2026_excludes_2027`, `current_2027_rebate_and_threshold`, `current_rebate_2027_banglish`, `unchanged_source_tax_across_chain` |
| Hard scope + scoped answer with notice | `hard_document_scope_authority` |
| Unknown / no-answer | `unknown_lunar_rule` |
| Mixed-language 2025 guidance | `mixed_document_bangla_retrieval`, `mixed_document_code_switched_retrieval` |

Schema v2 adds ordered sequences on the same corpus. Schema v1 packs without
sequences remain valid. The tax pack uses a fixed `reference_time` of
`2026-08-01T00:00:00Z` so “current” source-policy dates stay compatible with
the existing dated 2026 expectations. The harness patches only conversational
reference-date reads and the default source-policy date reads in `SearchService`
and `knowledge/source_metadata_read.py` around each turn. It does not freeze
database timestamps, ingest/index clocks, timeouts, polling, or `perf_counter`,
and it does not inject `as_of` into requests that omit it.

Each existing standalone case still gets a fresh conversation. Each sequence
gets one conversation, executes turns in order with a fresh session and
`ChatService`, and lets production persistence supply history. Assertion
failures are recorded and later turns still run. An execution failure that
prevents continuation blocks remaining turns. Variants remain isolated.
Request filters are never copied from a previous turn.

| Sequence | Intent |
| -------- | ------ |
| `adopt_then_replace_rebate` / `adopt_then_replace_reimbursement` | Continue a calculation, adopt its result, then replace the amount |
| `temporal_snapshot_clarify` | Change period, clarify “before that”, then answer at an exact snapshot |
| `clarify_then_short_answer` | Ambiguous reference, then a short disambiguating reply |
| `topic_reset_drops_amount` | Reset topic without carrying the old amount |
| `language_switch_unicode_correction` | Switch to Bangla and correct a Unicode-digit amount |
| `compare_scope_nonsticky_filters` | Compare sources, then prove omitted `document_id` / `metadata_filter` are not sticky |

The tax pack uses those keys on the tax corpus. `business_conversation_v1` repeats
the same intentions on expense-policy and support-plan documents. Request filters
stay non-sticky in production. Ambiguous adoption and “was that amount correct?”
verification stay in deterministic resolver unit tests, not extra Journey sequences.

Checked-in expected failing keys live in
`tests/fixtures/journeys/tax_v1/phase1_sequence_baseline.json`. That artifact is
the pre-resolver snapshot. Do not relax sequence assertions to match fallback
behavior. Provider-backed Journey runs are the development gate for sequence
pass/fail; echo-LLM smoke does not fake-pass `expected_resolution`.

## Development versus held-out scoring

Journey development sequences are not the held-out continuity set.

Before a later release evaluation:

1. Freeze the resolver prompt/version, validation rules, model/settings, and development fixtures.
2. Have an independent reviewer author unseen scenarios under a predeclared coverage rubric. Keep exact held-out examples outside implementation and tuning sessions until the candidate is frozen.
3. Include novel entities, amounts, wording, domain context, adoption patterns, corrections, ambiguity, standalone turns, and temporal references — not cosmetic rewrites of development cases.
4. Lock the dataset hash, scoring rules, and denominators before execution.
5. Score every applicable turn, including fallbacks and timeouts; report results by category and total sample size.

Target at least 90% structured resolution accuracy on that sample, with
clarification accuracy reported separately. That is a later release check on the
sample, not a universal reliability claim. Protocol, scoring rules, and
denominators: [Held-out turn-resolution evaluation](./turn_resolution_held_out.md).
If failures lead to tuning, the exposed set becomes development data and a fresh
held-out set is required.

## Measurements

`summary.md` reports resolver and continuity measurements for each variant:

| Measurement | Definition |
| ----------- | ---------- |
| Resolver latency | p50/p95 over attempted calls, including timeouts; total-turn latency is shown beside it |
| Usage/cost | Resolver tokens per attempted call and share of turn tokens. Cost is null unless a recorded rate card or reported charges exist |
| Fallback rate | Fallbacks / attempted resolutions, grouped by `failure_code`. Bypasses are separate |
| Standalone rate | Standalone or topic-change outcomes among attempted resolutions |
| Input-change rate | `query_changed` and `filter_changed` separately. Filter changes include a validated derived `as_of`; document and metadata scope never change |
| Retrieval-change rate | Optional `--replay-raw-retrieval` paired differences in retrieved set/rank and required-anchor recall |
| Continuity | Per-turn resolution correctness and complete-sequence success |

Resolution time is subtracted from residual “grounding and context” timing.
Replay runs after the production turn, uses the same Journey reference clock,
never generates, and never writes into conversation history. Snapshot mismatch
or provider degradation makes a pair non-comparable. Production still retrieves
once.

Evidence Quality stays a separate retrieval/grounding evaluation system. Its
`previous_user_query` field does not demonstrate conversation continuity. Do
not merge Journey sequences into Evidence Quality in this change.

## Production paths the journey exercises

These are product behaviors the cases observe. The harness does not reimplement them.

| Path | What the journey checks |
| ---- | ----------------------- |
| Hybrid retrieval | Original dense + original lexical always run. Optional one translated pair is additive and never cited |
| Query translation routing | Bangla → English and Banglish/code-switched → English when English exists in inventory. Ordinary English queries do not auto-translate to Bangla because Bangla exists in the corpus. Mixed-script queries keep both scripts and skip the rewrite. Hard-scoped same-language documents skip with `same_language_scope`; otherwise unused rewrites skip with `no_translation_target` |
| Translation budget | Default minimum output is 256 tokens (`APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS`), hard-capped at 2048. Empty/failed rewrites record `finish_reason`, output tokens, reasoning tokens, attempts, and validation reasons on retrieval diagnostics |
| Cross-language evidence | Dedicated `chat.cross_language_semantic_evidence_score_threshold` (default `0.30`). Must not exceed the semantic bar. Not lowered to pass this fixture |
| Candidate-wise grounding | Internal code behavior. It remains outside the journey allowlist and is not a universal invariant or Project switch |
| Grounding mode | V2 Project behavior `behavior.grounding_assurance` is `strict` or `balanced`; this is not a tax-specific switch |
| Context budget | Admitted units are indivisible. Overflow is `context_selection`, not a rewritten span. Rerank relevance is preserved onto selected units |
| Source policy / MODIFIES | Code applies governance automatically where source metadata/relationships exist; ungoverned Projects stay neutral. Scoped cases still expect `suppressed_document_scope` |
| Passage scoring | `execution.passage_scoring_enabled` stays **off** by default. Grounding may still run adaptive passage rescue on high-confidence near-misses |
| Web fallback | Indexed-only / sufficient indexed answers must not search the web. Hard scope and `as_of` suppress web search. `--set behavior.response_mode=…` is allowlisted for A/B only; do not use this corpus to certify web modes |

## Configuration

The CLI accepts only an explicit allowlist of query-time `ProjectAIConfig` leaves
(`RUNTIME_COMPARISON_CONFIG_KEYS`, aliased as `SAFE_CONFIG_KEYS` in
`rag_journey.py`). Index-affecting settings (embeddings, chunking, FTS) are
rejected. `--compare` and `--compare-translation` each add one second variant
and must change the effective configuration hash without changing the active
corpus fingerprint. They are mutually exclusive.

The allowlist contains only V2 `behavior.*` and `execution.*` leaves. It excludes
providers, raw calibration, web budgets, source policy, invariant controls, and
index identity. New Project AI leaves stay rejected until they are classified as
safe against an existing index. Full env meaning lives in the
[Configuration Map](../configuration-map.md).

Useful leaves for this journey:

```text
behavior.translation_policy
behavior.grounding_assurance
behavior.response_mode
execution.retrieval_top_k
execution.rerank_mode
execution.passage_scoring_enabled
execution.passage_window_tokens
execution.max_context_chunks
```

`--set` builds a **sparse** Project revision. Omitted leaves inherit deployment
settings. Query translation stays off unless inherited from
`APE_QUERY_TRANSLATION__ENABLED` or enabled by V2 `behavior.translation_policy`.

The journey does not itself alter source policy or MODIFIES expansion. Candidate-wise admission
is the only reranked path. These remain code-owned and conditional on source metadata.

Deployment settings the product path uses (not journey-only). Values are **code
defaults**; hosted example files override several:

| Setting | Code default | Role |
| ------- | ------------ | ---- |
| `APE_QUERY_TRANSLATION__ENABLED` | `false` | Global default for V2 `behavior.translation_policy`; Projects override Inherit / On / Off |
| `APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS` | `256` | Floor for retrieval-translation output |
| `APE_CHAT__CROSS_LANGUAGE_SEMANTIC_EVIDENCE_SCORE_THRESHOLD` | `0.30` | Cross-language semantic admit bar |
| `APE_CHAT__GROUNDING_MODE` | `strict` | Deployment grounding default; V2 can choose strict or balanced assurance |
| `APE_CHAT__HIGH_CONFIDENCE_RERANKER_EVIDENCE_SCORE` | `0.70` | High reranker bar for measurement-gated balanced admission and passage rescue; must exceed the medium reranker bar |
| `APE_CHAT__STORE_CANDIDATE_TRACE` | `false` | Debug-only per-candidate traces on chat messages. Evaluation rows always keep the detail. The journey runner enables traces in-process for scoring; it does not change this deployment default. |
| `APE_AI_POLICY__SOURCE_POLICY_MODE` | `enforce` | Code-owned source governance where metadata/relationships exist |
| `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` | `enforce` | Maximum allowed source-policy mode; restricts only, never activates |
| `APE_RETRIEVAL__MODIFIES_EXPANSION_MODE` | `expand` | Code-owned conditional incoming MODIFIES recall |
| `APE_RERANKER__REQUEST_TIMEOUT_SECONDS` | `10` | Fail-open rerank timeout. Hosted example: `30` |
| `APE_RETRIEVAL__PASSAGE_SCORING_ENABLED` | `false` | Always-on bounded-passage scoring; keep off unless measuring. Adaptive rescue is separate |

Safety flags: `--allow-nonlocal-database`, `--allow-nonlocal-storage`,
`--keep-project`. Without the allow flags, non-loopback PostgreSQL/MinIO hosts
fail closed before creating state.

The checked-in `rag_journey_phase1_baselines_v1.json` dataset adds measurement-only
hard-negative, translation-off, polarity, arithmetic, reranker-unavailable, and
multi-turn-follow-up cases. These record baselines and do not enable new heuristics;
the existing 21-case journey manifest remains unchanged.

## Commands

Unit checks from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest tests/unit/cli/test_rag_journey.py tests/unit/modules/conversations/test_turn_resolution.py tests/unit/modules/conversations/test_turn_resolution_safety.py tests/unit/modules/conversations/test_turn_resolver.py tests/unit/modules/conversations/test_chat_service.py tests/unit/modules/conversations/test_prompt_builder.py tests/unit/modules/conversations/test_prompt_registry.py tests/unit/modules/conversations/test_message_repository.py -q
backend\.venv\Scripts\python.exe -m ruff check backend/app/cli/rag_journey.py backend/app/cli/rag_journey_cli.py backend/app/modules/conversations/turn_resolution.py backend/app/modules/conversations/turn_resolver.py backend/app/modules/conversations/prompts/turn_resolution.py backend/app/modules/conversations/services/chat_service.py backend/app/modules/conversations/prompt_builder.py backend/app/modules/conversations/prompts/registry.py backend/app/modules/conversations/repositories/message_repository.py
backend\.venv\Scripts\python.exe -m mypy --no-incremental backend/app/cli/rag_journey.py backend/app/modules/conversations/turn_resolution.py backend/app/modules/conversations/turn_resolver.py backend/app/modules/conversations/prompts/turn_resolution.py backend/app/modules/conversations/services/chat_service.py
```

PostgreSQL/pgvector smoke from `backend/` (loads `.env`; skipped when `ape_test`
is unavailable):

```powershell
.venv\Scripts\python.exe -m pytest ..\tests\integration\test_rag_journey_smoke.py -q
```

Full production journey from `backend/`:

```powershell
.venv\Scripts\python.exe -m app.cli rag-journey
.venv\Scripts\python.exe -m app.cli rag-journey --fixture ..\tests\fixtures\journeys\business_conversation_v1\journey.json
.venv\Scripts\python.exe -m app.cli rag-journey --replay-raw-retrieval
```

Equivalent Makefile target from the repository root: `make rag-journey`
(`RAG_JOURNEY_ARGS` is forwarded).

Translation on/off A/B on the same Project, ingested corpus, and active index
(no rebuild):

```powershell
.venv\Scripts\python.exe -m app.cli rag-journey --compare-translation
```

This keeps the current configuration as `translation_on` and only sets
`behavior.translation_policy=disabled` for `translation_off`. `summary.md`
includes a paired quality/latency table. `results.json` keeps per-case timings,
`translation_changed_retrieval_outcome`, and verdicts. Translation must already
be enabled on the current configuration.

Optional query-time overrides and one-factor compare (does not change product
defaults):

```powershell
.venv\Scripts\python.exe -m app.cli rag-journey `
  --set behavior.grounding_assurance=balanced `
  --set execution.rerank_mode=cross_language `
  --set execution.retrieval_top_k=12 `
  --compare execution.passage_scoring_enabled=true
```

The full journey needs the configured database, object storage, embedding, and
generation providers, plus the default/local Organization. Provider connection
or rate-limit failures stop setup/indexing before case results; those are
environmental failures, not passing or failing assertions.

Exit codes: `0` all variants passed and cleanup succeeded (or `--keep-project`);
`1` case or cleanup failure; `2` harness/`JourneyError` (unsafe target, invalid
`--set`, missing Organization).

Reports: `artifacts/rag-journey/tax_v1/<timestamp>-<run-id>/results.json` and
`summary.md`. Pass rate, recall, and latency in `summary.md` are descriptive for
that local corpus and provider pair. They are not a universal production
optimum.

## Dependencies

- PostgreSQL + pgvector, object storage, and the default Organization
- Configured embedding, generation, optional rerank, and optional query-translation providers
- Process-local inline job transport (applied by the CLI; dispatcher off)
- Production modules: knowledge ingest, index lifecycle, retrieval, conversations, source metadata

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| Phrase anchors instead of chunk UUIDs | Survives rechunking; mixed 2025 guidance has no stable Markdown headings |
| `content_match_anchors` for mixed Bangla retrieval | Semantic chunking can split `VR-2025-APE` across neighboring windows; require the source phrase, not one UUID |
| OR groups for equivalent EN/BN/historical sources | Avoids forcing one language or one restatement of the same 15% fact |
| 2027 fixture omits `BDT 400,000` | Forces mixed-source composition instead of answering the threshold from 2027 text |
| Query-time `--set` / `--compare` / `--compare-translation` allowlist | Prevents accidental index rebuilds and undocumented threshold fishing. Translation A/B reuses that path instead of a second benchmark stack |
| Inline jobs + dispatcher off only in this process | Makes ingest/index/purge deterministic without a sidecar worker or outbox race |
| Purge modifiers before targets | Honors MODIFIES foreign keys; insertion order is not the lifecycle contract |
| Do not default passage scoring from this corpus | One local compare is not a calibration for other corpora |
| Do not treat hosted `.env` overrides as journey defaults | Compose may already enable balanced grounding, candidate-wise units, source-policy enforce, and MODIFIES expand |

## Production considerations

Do not tune embeddings, reranker thresholds, models, or generation prompts to
make this journey pass. Raw retrieval may be broad; final evidence must obey
effective date, hard scope, provision authority, citation, provenance,
grounding, refusal, and false-accept protections.

`hosted_managed` examples currently use Cohere `embed-v4.0` / `rerank-v4.0-pro`,
OpenAI `gpt-5.6-luna`, and `gpt-5-nano` translation. Reranker unavailability
falls back to fused order; empty nano translations persist as failed/skipped
diagnostics rather than as a reason to raise token budgets further without
measuring `finish_reason` / reasoning tokens.

Temporary Projects are tagged `rag-journey:<uuid>` and purged unless
`--keep-project`. Cleanup is part of the pass contract.

## Testing strategy

- Unit: `tests/unit/cli/test_rag_journey.py` — manifest shape, 21 standalone tax cases, business pack sequences, schema v1 compatibility, sequence validation, `reference_time` clock, clarification scoring, resolution measurements, residual timing, raw-retrieval replay comparison, `--replay-raw-retrieval`, historical OR group, 2027 threshold composition, chunking paths (markdown vs semantic mixed guidance), assertion stages, semantic token groups, user-parameter tokens, `content_match_anchors`, provider-degradation reporting, `--set`/`--compare`/`--compare-translation` allowlist, translation A/B verdicts, purge order, Organization preflight
- Unit: `tests/unit/modules/conversations/test_turn_resolution.py` — adoption provenance, parameter replacement, Unicode normalization, UTC midnight / leap-day / month-boundary dates, conflicting `as_of`, citation-date non-authorization, JSON parse, history bounding
- Unit: `tests/unit/modules/conversations/test_turn_resolution_safety.py` — verification is not adoption, citation dates cannot authorize snapshots, request filters stay non-sticky
- Unit: `tests/unit/modules/conversations/test_turn_resolver.py` / `test_chat_service.py` — one-call resolution, fallback, cancellation, clarification `grounded=null`, effective retrieval inputs
- Unit: `tests/unit/modules/conversations/test_grounding_modes.py` — strict vs balanced monotonic admission, additive high-confidence/passage rescue, authority fallthrough, Bangla query scaffolding
- Unit: `tests/unit/modules/conversations/test_current_authority.py` — Bangla `ধারা` / `বিধি` provision redaction
- Integration: `tests/integration/test_rag_journey_smoke.py` — tax and business subsets on real PostgreSQL/pgvector with deterministic fixture embedders; sequence harness reuses one conversation without requiring a resolver LLM. Does not call hosted LLMs
- Full CLI: real providers; optional `--compare`, `--compare-translation`, `--replay-raw-retrieval`, `--fixture`

## Future improvements

- Treat remaining local misses as product-path observations (citation of required mixed-source groups, scoped wording), not as harness relaxations
- Use `--compare-translation` measurements to decide whether translation stays always-on, becomes selective, or moves to a fallback/rescue path. Do not optimize translation from a single local corpus.
- A hosted/operator-console runner is out of scope; this remains a local CLI with an integration smoke

## Related

- [Configuration Map](../configuration-map.md)
- [Conversations](./conversation_module.md)
- [Multilingual support](./multilingual_support.md)
- [Retrieval](./retrieval_module.md)
- [Evidence quality](./evidence_quality.md)
- [Held-out turn-resolution evaluation](./turn_resolution_held_out.md)
- [Safe corpus/index lifecycle](./safe_corpus_index_lifecycle.md)
- [ADR-018 multilingual retrieval](../architecture/adr/018-multilingual-retrieval-v1.md)
- [Conversation RAG journey (learning)](../learning/conversation_rag_journey.md)
