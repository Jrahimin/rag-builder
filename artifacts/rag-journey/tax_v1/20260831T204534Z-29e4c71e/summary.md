# RAG Journey: tax_v1

- Status: **PASSED**
- Run ID: `29e4c71e-d9aa-4ec2-bb49-8bbccc3823b3`
- Project ID: `5268d195-4c84-4b2d-a0d6-3919cabcb91f`
- Job transport: inline (configured: taskiq)
- Cleanup: succeeded

## Corpus

Active build `08b7ae0d-b071-4a3c-904a-d2873eb17070`: 2 documents, 9 chunks, 9 vectors, 9 lexical entries.

## baseline

LLM `openai/gpt-5.6-luna`; embedding `cohere/embed-v4.0`; reranker `cohere/rerank-v4.0-pro`; translation `openai/gpt-5-nano`. Effective config hash `0bcd72b326b6e85d8f5be24974a327fe936af3410e8211c6db6c1cd5780fbae1`.

Passed 10/10 (100%); mean recall 1.000; p50/p95 3678/11100 ms.

| Case | Tags | Result | Failure stage(s) | Total ms |
|---|---|---:|---|---:|
| `eligible_investments_scoped` | factual, scope | PASS | — | 3709 |
| `current_rebate_calculation` | authority, calculation | PASS | — | 6987 |
| `historical_rebate_rate` | authority, historical | PASS | — | 2539 |
| `current_rebate_bangla` | multilingual, authority | PASS | — | 11100 |
| `current_rebate_banglish` | multilingual, authority | PASS | — | 6117 |
| `stale_rebate_correction` | authority, stale_rule | PASS | — | 3648 |
| `current_threshold` | authority, factual | PASS | — | 5309 |
| `unchanged_source_tax` | authority, unchanged | PASS | — | 3521 |
| `hard_document_scope_authority` | scope, authority | PASS | — | 857 |
| `unknown_lunar_rule` | refusal, fallback | PASS | — | 931 |

## Notes

Performance is descriptive for this local run. Configuration comparisons apply only to this `tax_v1` corpus and do not establish a universal production optimum.
