# `tax_v1` RAG journey

## Purpose

`tax_v1` is an end-to-end regression journey for factual tax conversations. It runs the production ingestion, indexing, retrieval, authority resolution, grounding, citation, scope, refusal, multilingual, and cleanup paths.

The journey keeps the original 10 cases and adds focused coverage for bilingual sources, mixed-source answers, user-supplied amounts, temporal authority through 2027, unchanged provisions, and mixed-language semantic documents. The manifest currently contains 21 cases.

## Corpus and authority graph

The fixture corpus is under `tests/fixtures/journeys/tax_v1/corpus/`:

- `tax_2023`: English 2023 Act.
- `tax_2023_bn`: equivalent Bangla 2023 Act; parallel to `tax_2023`, not a modifier.
- `tax_rules_2024_bn`: complementary procedural Rules; it has no `MODIFIES` relationship.
- `tax_guidance_2025`: supplementary mixed-language procedural guidance. Production language detection classifies it as `mixed`, so ingestion follows the normal **semantic chunking** path rather than Markdown heading splits. It has no `MODIFIES` relationship and must not override 2023/2024/2026/2027 substantive authority.
- `finance_2026`: modifies Sections 10, 21, and 40 of both 2023 language sources.
- `finance_2027`: effective 2027-07-01; modifies the relevant 2026 rebate/example/authority provisions. The 2026 threshold and unchanged source-tax rule remain effective.

The resulting authority chain is:

`2023 Act (EN/BN) → Finance 2026 → Finance 2027`

The 2025 mixed-language guidance sits beside that chain as a procedural source only.

Authority is provision-scoped. A newer document does not replace an entire older document, and parallel translations are not conflicting amendments.

Mixed-document journey anchors resolve by unique content phrases such as `VR-2025-APE` and `14 calendar days`. They do not depend on Markdown headings or exact semantic chunk boundaries.

## Assertion model

Case definitions are in `tests/fixtures/journeys/tax_v1/journey.json`.

- `expected_tokens` checks required normalized facts, including Unicode/Bangla digits.
- `expected_any` accepts equivalent wording for discourse or unchanged-rule markers.
- `required_anchor_groups` uses OR within a group and AND across groups. Each required group must be retrieved, admitted, and used by grounded claim evidence. This supports equivalent EN/BN alternatives while enforcing every source family in mixed-source answers.
- Mixed-document cases (`mixed_document_bangla_retrieval`, `mixed_document_code_switched_retrieval`) require the 2025 guidance via phrase-only anchors. The code-switched case also requires production `query_language_profile` translation diagnostics; it does not require translation to be applied.
- `prohibited_final_sources` rejects genuinely future documents from final admitted/cited/claim evidence. Older sources are not globally banned because unchanged provisions can remain valid. Historical 15% questions may cite the 2023 Act, the 2024 Rules clarification that the 15% rate was unchanged on 1 January 2024, or the 2026 Finance Act sentence that explicitly preserves the pre-1 July 2026 15% rule; 2027 remains prohibited for a 2024 `as_of`.
- `current_2027_rebate_and_threshold` requires 2027 evidence for the rebate and 2026 evidence for the still-effective threshold. The 2027 fixture references that 2026 threshold without restating `BDT 400,000`.
- `prohibited_answer_tokens` protects cases where a supplied value must not be replaced by a fixture example.
- Hard-scoped current queries must distinguish the scoped document’s historical value from unavailable current authority.

All factual claims remain independently verified; `grounded` still means every factual claim is supported. Citation and provenance requirements are not relaxed.

## Production files

- `backend/app/cli/rag_journey.py` — manifest loading, case evaluation, anchor-group checks, scope/future-source checks, reporting, and production journey orchestration.
- `backend/app/modules/conversations/current_authority.py` — provision-scoped redaction, including Bangla `ধারা` and `বিধি` headings.
- `tests/unit/cli/test_rag_journey.py` — manifest, assertion, alternative-source, and scope regressions.
- `tests/unit/modules/conversations/test_current_authority.py` — exact Bangla provision redaction.
- `tests/integration/test_rag_journey_smoke.py` — PostgreSQL/pgvector production-path smoke and cleanup checks.

Reports are written to `artifacts/rag-journey/tax_v1/<run-id>/` as `results.json` and `summary.md`.

## Commands

Run from the repository root for unit checks:

```powershell
backend\.venv\Scripts\python.exe -m pytest tests/unit/cli tests/unit/modules/conversations -q
backend\.venv\Scripts\ruff.exe check backend/app/cli/rag_journey.py backend/app/modules/conversations/current_authority.py tests/unit/cli/test_rag_journey.py tests/unit/modules/conversations/test_current_authority.py tests/integration/test_rag_journey_smoke.py --no-cache
backend\.venv\Scripts\mypy.exe --no-incremental backend/app/cli/rag_journey.py backend/app/modules/conversations/current_authority.py
```

Run the integration smoke from `backend/` so its environment is loaded:

```powershell
.venv\Scripts\python.exe -m pytest ..\tests\integration\test_rag_journey_smoke.py -q
```

Run the full production journey from `backend/`:

```powershell
.venv\Scripts\python.exe -m app.cli rag-journey
```

The full journey requires the configured database, storage, embedding, and generation providers. Provider connection or rate-limit failures stop setup/indexing before case results; they are environmental failures, not passing or failing case assertions.

## Scope constraints

Do not tune embeddings, reranker thresholds, models, or generation prompts to make this journey pass. Raw retrieval may be broad; final evidence must obey effective date, hard scope, provision authority, citation, provenance, grounding, refusal, and false-accept protections.
