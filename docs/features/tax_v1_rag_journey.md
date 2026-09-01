# `tax_v1` RAG journey

## Purpose

`tax_v1` is a local, production-path regression journey for factual tax conversations. It creates a temporary Project, ingests the synthetic corpus through the real knowledge/index lifecycle, then runs 21 chat turns against the same retrieval, authority, grounding, citation, scope, refusal, and multilingual path used by the product API.

It is not an evaluation dataset and does not replace Evidence Quality runs. It exists to catch regressions in the composed RAG path on a known bilingual authority graph. The harness may `--set` or `--compare` query-time Project AI leaves; it must not lower evidence thresholds, weaken citations, or rewrite fixtures so a local run can report 21/21.

## Architecture

```text
python -m app.cli rag-journey
        │
        ├── force APE_JOBS__BACKEND=inline (this process only)
        ├── preflight: loopback DB/storage, default Organization
        ├── Project + sparse Project AI revision
        ├── ingest 6 sources (MODIFIES + provision scopes)
        ├── immutable index build + activate
        ├── phrase-anchor → chunk mapping
        ├── baseline cases via ChatService
        ├── optional one-factor --compare (same corpus fingerprint)
        ├── artifacts/rag-journey/tax_v1/<run-id>/
        └── relationship-aware purge (unless --keep-project)
```

| Component | Role |
| --------- | ---- |
| `backend/app/cli/rag_journey_cli.py` | Argument parsing, inline job override, exit codes |
| `backend/app/cli/rag_journey.py` | Manifest, orchestration, assertions, reports, cleanup |
| `tests/fixtures/journeys/tax_v1/journey.json` | Sources, phrase anchors, 21 cases |
| Knowledge + retrieval workflows | Upload → parse → chunk → embed → index, including per-document language inventory on the build manifest |
| `ChatService` / `GroundingService` / `current_authority` | Production message path the cases assert against |

The runner is a thin operator tool. It does not implement a second RAG stack. Cases call the same conversation send-message path, including `as_of`, hard `document_id` scope, query-language routing, and retrieval diagnostics.

## Data flow

```text
preflight
  → create Project on the default Organization
  → activate baseline ProjectAIConfig (--set leaves only)
  → ingest corpus with source metadata / MODIFIES
  → wait for active vector + lexical index
  → map evidence anchors by unique phrases (not chunk UUIDs)
  → for each case: create conversation → send query → evaluate stages
  → optional --compare: one new AI revision, same index, second variant
  → write results.json + summary.md
  → purge modifiers before targets, then delete the Project
```

Jobs in the CLI process use inline durable-job transport even when the deployment is configured for Taskiq. That override is process-local and restored on exit.

## Corpus and authority graph

Fixture files live under `tests/fixtures/journeys/tax_v1/corpus/`. A representative hosted run indexes 6 documents into about 25 markdown/semantic chunks.

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

Authority is provision-scoped. A newer document does not replace an entire older document. Parallel EN/BN 2023 texts are translations, not conflicting amendments. `finance_2027` does not modify the 2026 tax-free threshold or the savings-certificate source-tax provision, and the 2027 fixture **does not restate** `BDT 400,000`. Composed 2027 rebate + threshold answers must therefore retrieve both the 2027 rebate provision and the still-effective 2026 threshold.

Mixed-document journey anchors resolve by unique content phrases such as `VR-2025-APE` and `14 calendar days`. They do not depend on Markdown headings or exact semantic chunk boundaries.

Index builds record per-document `document_language` / chunk language counts. Hard-scoped retrieval uses that inventory so same-language documents do not spend a translation call.

## Assertion model

Case definitions are in `tests/fixtures/journeys/tax_v1/journey.json`.

| Field | Behavior |
| ----- | -------- |
| `expected_tokens` | Required normalized facts, including Unicode/Bangla digits |
| `expected_token_groups` | AND across groups, OR within a group. Accepts semantically equivalent wording such as `savings certificates` vs `approved savings certificates` |
| `expected_any` | At least one equivalent wording (discourse / unchanged-rule markers) |
| `user_parameter_tokens` | Values supplied in the user query (for example BDT 75,000). Must appear in the answer; they do not require a knowledge-base citation by themselves. Rules and rates used to calculate from that parameter still need retrieved evidence |
| `required_anchor_groups` | OR within a group, AND across groups. Each group must be retrieved, admitted, and used by grounded claim evidence |
| `prohibited_final_sources` | Rejects listed documents from admitted/cited/claim evidence only |
| `prohibited_answer_tokens` | User-supplied amounts must not be replaced by fixture examples |
| `correction` | Stale-claim cases must state the new facts plus a correction marker; repeating the old tokens is not required |
| `mode` | `answerable` (default), `scope_isolation`, or `no_answer` |
| `document_scope` / `as_of` | Production hard scope and effective date |

Failure stages localize where the production path broke: `retrieval`, `admission_grounding`, `context_selection`, `citation`, `generation_refusal`, `authority`, `fallback`. `admitted_count > 0` with `context_selected_count == 0` is `context_selection` (`authority_context_empty` or `context_selection_empty`), not `admission_grounding`.

Reports split **correctness**, **provider degradation**, and **latency**. A reranker timeout or rate limit is `rerank_status=unavailable` with a sanitized `failure_reason`; it is not a semantic RAG failure.

Harness-only details that must stay in the fixture, not in production thresholds:

- **Historical 15% (`historical_rebate_rate`).** One valid source is enough: 2023 Act EN, 2023 Act BN, the 2024 Rules clarification that the 15% rate was unchanged on 1 January 2024 (`historical_rebate_rate_2024_bn`), or the 2026 sentence that the previous 15% rate remains relevant only for historical questions. `finance_2027` stays prohibited for a 2024 `as_of`. Older sources are not globally banned because unchanged provisions can remain valid.
- **Historical bilingual (`historical_rebate_bilingual`).** Any of 2023 EN, 2023 BN, or the 2024 Rules historical restatement is enough. `finance_2026` and `finance_2027` stay prohibited as final sources.
- **`current_2027_rebate_and_threshold`.** Requires 2027 evidence for the rebate **and** 2026 evidence for the still-effective threshold.
- **Mixed-document cases.** Phrase-only 2025 guidance anchors. The code-switched case requires production `query_language_profile` translation diagnostics; it does not require a rewrite to be applied.
- **Declared 75,000 (`declared_investment_75000`).** The amount is a user-supplied parameter. The case requires the 2026 rebate-rate evidence and the calculated 10% / 7,500 result; it does not require citing the 2024 Rules example amount merely to prove the input. Prohibited tokens still catch substitution of the fixture's 60,000 example.
- **Hard-scoped current queries.** Must distinguish the scoped document’s historical value from unavailable current authority (`unavailable_within_hard_scope` / `suppressed_document_scope` when MODIFIES expansion is on).

All factual claims remain independently verified. `grounded` still means every factual claim is supported. Citation and provenance requirements are not relaxed.

## Case coverage

The manifest keeps the original 10 cases and adds bilingual, mixed-source, temporal, user-amount, and mixed-document coverage. Total: **21**.

| Intent | Cases |
| ------ | ----- |
| Scoped facts | `eligible_investments_scoped` |
| Current 2026 rebate / threshold / source tax | `current_rebate_calculation`, `current_threshold`, `unchanged_source_tax` |
| Historical 15% | `historical_rebate_rate`, `historical_rebate_bn_scoped`, `historical_rebate_bilingual` |
| Bangla / Banglish current rebate | `current_rebate_bangla`, `current_rebate_banglish` |
| Stale 15% correction | `stale_rebate_correction` |
| Mixed 2026 rate + 2024 Rules evidence / declared amount | `current_rebate_with_savings_evidence_bn`, `declared_investment_75000` |
| 2027 chain | `current_rebate_2027`, `rebate_as_of_2026_excludes_2027`, `current_2027_rebate_and_threshold`, `current_rebate_2027_banglish`, `unchanged_source_tax_across_chain` |
| Hard scope + refusal | `hard_document_scope_authority`, `unknown_lunar_rule` |
| Mixed-language 2025 guidance | `mixed_document_bangla_retrieval`, `mixed_document_code_switched_retrieval` |

## Production paths the journey exercises

These are product behaviors the cases observe. The harness does not reimplement them.

| Path | What `tax_v1` checks |
| ---- | -------------------- |
| Hybrid retrieval | Original dense + original lexical always run. Optional one translated pair is additive and never cited |
| Query translation routing | Bangla → English and Banglish/code-switched → English when English exists in inventory. Ordinary English queries do not auto-translate to Bangla because Bangla exists in the corpus. Mixed-script queries keep both scripts and skip the rewrite. Hard-scoped same-language documents skip with `same_language_scope`; otherwise unused rewrites skip with `no_translation_target` |
| Translation budget | Default minimum output is 256 tokens (`APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS`), hard-capped at 2048. Empty/failed rewrites record `finish_reason`, output tokens, reasoning tokens, attempts, and validation reasons on retrieval diagnostics |
| Cross-language evidence | Dedicated `chat.cross_language_semantic_evidence_score_threshold` (default `0.30`). Must not exceed the semantic bar. Not lowered to pass this fixture |
| Candidate-wise grounding | When enabled, admitted `EvidenceUnit`s drive generation; when off, assessments remain shadow-only. Toggle is query-time (`chat.candidate_wise_grounding_enabled`) |
| Grounding mode | `chat.grounding_mode=strict` (default, high-assurance corroboration) or `balanced` (high-confidence reranker near-miss may admit). Not a tax-specific switch |
| Source policy / MODIFIES | `source_policy_mode` (inherits `APE_AI_POLICY__SOURCE_POLICY_MODE`, default `off`) and `retrieval.modifies_expansion_mode` are query-time. Expansion-on scoped cases expect `suppressed_document_scope` |
| Passage scoring | Always-on `retrieval.passage_scoring_enabled` stays **off** by default. Grounding may still run adaptive passage rescue on high-confidence near-misses. One-factor `--compare` of always-on scoring is allowed for debugging |
| Web fallback | Indexed-only / sufficient indexed answers must not search the web. Hard scope and `as_of` suppress web search |

## Configuration

The CLI accepts only an explicit allowlist of query-time `ProjectAIConfig` leaves (`SAFE_CONFIG_KEYS` in `rag_journey.py`). Index-affecting settings (embeddings, chunking, FTS) are rejected. `--compare` accepts exactly one assignment and must change the effective configuration hash without changing the active corpus fingerprint.

Useful leaves for this journey:

```text
source_policy_mode
retrieval.modifies_expansion_mode
retrieval.query_translation_enabled
retrieval.passage_scoring_enabled
chat.candidate_wise_grounding_enabled
chat.cross_language_semantic_evidence_score_threshold
chat.evidence_gate_mode
chat.grounding_mode
chat.high_confidence_reranker_evidence_score
```

`--set` builds a **sparse** Project revision. Omitted leaves inherit deployment settings (`hosted_managed` typically uses Cohere embed/rerank, OpenAI `gpt-5.6-luna` generation, and `gpt-5-nano` translation). The journey does not enable candidate-wise grounding, MODIFIES expansion, or source-policy enforce unless those leaves are set or already inherited from env (`APE_AI_POLICY__SOURCE_POLICY_MODE`, `APE_RETRIEVAL__MODIFIES_EXPANSION_MODE`, `APE_CHAT__CANDIDATE_WISE_GROUNDING_ENABLED`).

Deployment settings the product path uses (not journey-only):

| Setting | Default | Role |
| ------- | ------- | ---- |
| `APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS` | `256` | Floor for retrieval-translation output |
| `APE_CHAT__CROSS_LANGUAGE_SEMANTIC_EVIDENCE_SCORE_THRESHOLD` | `0.30` | Cross-language semantic admit bar |
| `APE_CHAT__GROUNDING_MODE` | `strict` | High-assurance corroboration; existing Projects inherit this |
| `APE_CHAT__HIGH_CONFIDENCE_RERANKER_EVIDENCE_SCORE` | `0.70` | Balanced near-miss and passage-rescue bar; must exceed the medium reranker bar |
| `APE_AI_POLICY__SOURCE_POLICY_MODE` | `off` | Deployment default for source-policy `off / observe / enforce`; existing Projects inherit this |
| `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` | `enforce` | Maximum allowed source-policy mode; restricts only, never activates |
| `APE_RERANKER__REQUEST_TIMEOUT_SECONDS` | `10` | Fail-open rerank timeout |
| `APE_RETRIEVAL__PASSAGE_SCORING_ENABLED` | `false` | Always-on bounded-passage scoring; keep off unless measuring. Adaptive rescue is separate |

Safety flags: `--allow-nonlocal-database`, `--allow-nonlocal-storage`, `--keep-project`. Without the allow flags, non-loopback PostgreSQL/MinIO hosts fail closed before creating state.

## Commands

Unit checks from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest tests/unit/cli tests/unit/modules/conversations -q
backend\.venv\Scripts\python.exe -m ruff check backend/app/cli/rag_journey.py backend/app/modules/conversations/current_authority.py tests/unit/cli/test_rag_journey.py tests/unit/modules/conversations/test_current_authority.py tests/integration/test_rag_journey_smoke.py
backend\.venv\Scripts\python.exe -m mypy --no-incremental backend/app/cli/rag_journey.py backend/app/modules/conversations/current_authority.py
```

PostgreSQL/pgvector smoke from `backend/` (loads `.env`; skipped when `ape_test` is unavailable):

```powershell
.venv\Scripts\python.exe -m pytest ..\tests\integration\test_rag_journey_smoke.py -q
```

Full production journey from `backend/`:

```powershell
.venv\Scripts\python.exe -m app.cli rag-journey
```

Equivalent Makefile target from the repository root: `make rag-journey` (`RAG_JOURNEY_ARGS` is forwarded).

Optional query-time overrides and one-factor compare (does not change product defaults):

```powershell
.venv\Scripts\python.exe -m app.cli rag-journey `
  --set source_policy_mode=enforce `
  --set retrieval.modifies_expansion_mode=expand `
  --set chat.candidate_wise_grounding_enabled=true `
  --set chat.grounding_mode=balanced `
  --compare retrieval.passage_scoring_enabled=true
```

The full journey needs the configured database, object storage, embedding, and generation providers, plus the default/local Organization. Provider connection or rate-limit failures stop setup/indexing before case results; those are environmental failures, not passing or failing assertions.

Exit codes: `0` all variants passed and cleanup succeeded (or `--keep-project`); `1` case or cleanup failure; `2` harness/`JourneyError` (unsafe target, invalid `--set`, missing Organization).

Reports: `artifacts/rag-journey/tax_v1/<timestamp>-<run-id>/results.json` and `summary.md`. Pass rate, recall, and latency in `summary.md` are descriptive for that local corpus and provider pair. They are not a universal production optimum.

## Dependencies

- PostgreSQL + pgvector, object storage, and the default Organization
- Configured embedding, generation, optional rerank, and optional query-translation providers
- Process-local inline job transport (applied by the CLI)
- Production modules: knowledge ingest, index lifecycle, retrieval, conversations, source metadata

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| Phrase anchors instead of chunk UUIDs | Survives rechunking; mixed 2025 guidance has no stable Markdown headings |
| OR groups for equivalent EN/BN/historical sources | Avoids forcing one language or one restatement of the same 15% fact |
| 2027 fixture omits `BDT 400,000` | Forces mixed-source composition instead of answering the threshold from 2027 text |
| Query-time `--set` / `--compare` allowlist | Prevents accidental index rebuilds and undocumented threshold fishing |
| Inline jobs only in this process | Makes ingest/index/purge deterministic without a sidecar worker |
| Purge modifiers before targets | Honors MODIFIES foreign keys; insertion order is not the lifecycle contract |
| Do not default passage scoring from this corpus | One local compare is not a calibration for other corpora |

## Production considerations

Do not tune embeddings, reranker thresholds, models, or generation prompts to make this journey pass. Raw retrieval may be broad; final evidence must obey effective date, hard scope, provision authority, citation, provenance, grounding, refusal, and false-accept protections.

`hosted_managed` examples currently use Cohere `embed-v4.0` / `rerank-v4.0-pro`, OpenAI `gpt-5.6-luna`, and `gpt-5-nano` translation. Reranker unavailability falls back to fused order; empty nano translations persist as failed/skipped diagnostics rather than as a reason to raise token budgets further without measuring `finish_reason` / reasoning tokens.

Temporary Projects are tagged `rag-journey:<uuid>` and purged unless `--keep-project`. Cleanup is part of the pass contract.

## Testing strategy

- Unit: `tests/unit/cli/test_rag_journey.py` — manifest shape, 21 cases, historical OR group, 2027 threshold composition, chunking paths (markdown vs semantic mixed guidance), assertion stages, semantic token groups, user-parameter tokens, provider-degradation reporting, `--set`/`--compare` allowlist, purge order, Organization preflight
- Unit: `tests/unit/modules/conversations/test_grounding_modes.py` — strict vs balanced monotonic admission, additive high-confidence/passage rescue, authority fallthrough, Bangla query scaffolding
- Unit: `tests/unit/modules/conversations/test_current_authority.py` — Bangla `ধারা` / `বিধি` provision redaction
- Integration: `tests/integration/test_rag_journey_smoke.py` — subset of cases on real PostgreSQL/pgvector with a deterministic fixture embedder; asserts diagnostics, hard scope, refusal, and cleanup. Does not call hosted LLMs
- Full CLI: real providers; optional `--compare`

## Future improvements

- Treat remaining local misses as product-path observations (citation of required mixed-source groups, scoped wording), not as harness relaxations
- Keep passage scoring configurable until a corpus-independent calibration exists
- A hosted/operator-console runner is out of scope; this remains a local CLI with an integration smoke

## Related

- [Conversations](./conversation_module.md)
- [Multilingual support](./multilingual_support.md)
- [Retrieval](./retrieval_module.md)
- [Evidence quality](./evidence_quality.md)
- [Safe corpus/index lifecycle](./safe_corpus_index_lifecycle.md)
- [ADR-018 multilingual retrieval](../architecture/adr/018-multilingual-retrieval-v1.md)
- [Conversation RAG journey (learning)](../learning/conversation_rag_journey.md)
