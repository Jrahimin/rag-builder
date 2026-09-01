# RAG Journey: tax_v1

- Status: **FAILED**
- Run ID: `1507e7fd-c21c-46b0-b263-a712048a9f85`
- Project ID: `10d03e68-d9e8-4d6e-bc34-87a316fbaff5`
- Job transport: inline (configured: taskiq)
- Cleanup: succeeded

## Corpus

Active build `256ab4ce-8e73-4b5e-bc9e-3bf0dd4e36ec`: 6 documents, 25 chunks, 25 vectors, 25 lexical entries.

## baseline

LLM `openai/gpt-5.6-luna`; embedding `cohere/embed-v4.0`; reranker `cohere/rerank-v4.0-pro`; translation `openai/gpt-5-nano`. Effective config hash `aa21aba9e8c5811db8f60bec3beebaa0476b4dedbbd57fd0f1a04cde37ebc6df`.

Passed 20/21 (95%); mean recall 0.987; p50/p95 4238/32397 ms.

| Case | Tags | Result | Failure stage(s) | Total ms |
|---|---|---:|---|---:|
| `eligible_investments_scoped` | factual, scope | PASS | — | 20087 |
| `current_rebate_calculation` | authority, calculation | PASS | — | 5092 |
| `historical_rebate_rate` | authority, historical | PASS | — | 3493 |
| `current_rebate_bangla` | multilingual, authority | PASS | — | 6941 |
| `current_rebate_banglish` | multilingual, authority | PASS | — | 15730 |
| `stale_rebate_correction` | authority, stale_rule | PASS | — | 11282 |
| `current_threshold` | authority, factual | PASS | — | 3505 |
| `unchanged_source_tax` | authority, unchanged | PASS | — | 4238 |
| `historical_rebate_bn_scoped` | multilingual, authority, historical, scope | FAIL | admission_grounding, generation_refusal, admission_grounding, citation, admission_grounding, admission_grounding, citation | 2068 |
| `historical_rebate_bilingual` | multilingual, authority, historical | PASS | — | 2334 |
| `current_rebate_with_savings_evidence_bn` | multilingual, authority, mixed_source | PASS | — | 3391 |
| `declared_investment_75000` | multilingual, authority, calculation, mixed_source | PASS | — | 3090 |
| `current_rebate_2027` | authority, calculation | PASS | — | 4948 |
| `rebate_as_of_2026_excludes_2027` | authority, historical, calculation | PASS | — | 6209 |
| `current_2027_rebate_and_threshold` | authority, mixed_source | PASS | — | 3824 |
| `current_rebate_2027_banglish` | multilingual, authority, calculation | PASS | — | 8181 |
| `unchanged_source_tax_across_chain` | authority, unchanged, mixed_source | PASS | — | 32397 |
| `hard_document_scope_authority` | scope, authority | PASS | — | 1116 |
| `unknown_lunar_rule` | refusal, fallback | PASS | — | 1120 |
| `mixed_document_bangla_retrieval` | multilingual, mixed_document | PASS | — | 38245 |
| `mixed_document_code_switched_retrieval` | multilingual, mixed_document, codeswitch | PASS | — | 2579 |

Failure details:

- `historical_rebate_bn_scoped` / `admission_grounding`: Expected retrieved evidence was not admitted.
- `historical_rebate_bn_scoped` / `generation_refusal`: Answer is missing expected fact '15%'.
- `historical_rebate_bn_scoped` / `admission_grounding`: Required evidence group was not admitted: rebate_rate_2023_bn.
- `historical_rebate_bn_scoped` / `citation`: No grounded claim used required evidence group: rebate_rate_2023_bn.
- `historical_rebate_bn_scoped` / `admission_grounding`: Answerable case was not grounded.
- `historical_rebate_bn_scoped` / `admission_grounding`: Indexed evidence did not pass the grounding gate.
- `historical_rebate_bn_scoped` / `citation`: No citation points at expected evidence.

## Notes

Performance is descriptive for this local run. Configuration comparisons apply only to this `tax_v1` corpus and do not establish a universal production optimum.
