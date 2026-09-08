# Tax refusal investigation — 9 September 2026

## Status

The demonstrated personal-input false-refusal bug has a narrow local runtime correction. **The complete salary-plus-interest scenario has not passed end-to-end acceptance.** Do not describe this patch as a complete mixed-income tax fix or deploy it with that claim. No live deployment or live policy was changed.

## What the supplied live trace shows

Conversation `1c31a272-d682-42f8-911e-44eef8f1ae17` used configuration revision 8, source generation 37 and the expected `v15-authoritative-compat` runtime. Generation never ran. Total processing was 82,121 ms; retrieval/preparation was 82,062 ms. There was no provider error, timeout or evidence of a failed deployment/index in this original trace.

The initial planner used eight searches for four bilingual concepts: employment exclusion, bands, rebate and minimum tax. It omitted the financial-income components. Two focused rounds followed. All twelve final source checks were marked supported and their ranges were valid. Coverage nevertheless remained incomplete because unknown personal assets, motorcars and large house property were listed as missing rules. The platform then emitted the generic unresolved-authority refusal.

That is a real distinction: source evidence can establish a conditional rule while the user's facts needed to apply it remain unknown. The original salary-only tests did not establish reliable behavior for additional income heads or these surcharge inputs.

## Retained correction

- Keep the original authoritative planning, coverage and focused-search prompts, discovery protocol, source presentation, two follow-ups and 120-second deadline.
- Only an incomplete verdict with valid exact proof for **every** source check may request a separate classification of its remaining gaps.
- Classify every gap by index. Only exclusively personal inputs permit a supported conditional answer; missing, duplicated, mixed or uncertain classifications cannot enable generation. Missing formulas, treatment, periods and amendment effects remain source gaps.
- The ordinary coverage reviewer cannot populate `missing_inputs` itself. Its unresolved issues must remain blocking gaps until the separate review accepts them.
- Pass accepted personal gaps to generation as untrusted analysis. Require a clearly scoped subtotal or conditional answer and a concise request for missing facts. Never assume a missing surcharge/credit is zero, present a subtotal as the final balance, or invent a missing rule.
- Persist the gaps in coverage diagnostics and input provenance. Runtime version: `v16-authoritative-conditional`.
- On an explicitly truncated planning or input-classification response, allow one restart at up to 2,048 output tokens, subject to the configured ceiling and existing deadline. Do not salvage partial JSON. Record retries and combined usage.

No tax rates, expected totals or tax-specific rule exceptions were added to Python. Successful ordinary generation payloads remain unchanged when no personal gap is identified.

## Validation and unresolved failures

The isolated provider classification evaluation passed **9/9** checks: three repetitions each of the original personal-assets gap, a genuine missing-rule gap and a mixed personal/rule gap. Tests verify exact proof, complete index accounting, failed classifications, unknown usage, untrusted prompt boundaries, generation handoff and persisted provenance.

Final retained-code verification: **1,131 backend unit and architecture tests passed** (20 existing deprecation warnings); Ruff lint and formatting checks passed; Mypy passed for the 30 conversation-module source files. These code checks do not replace the failed full-scenario acceptance below.

The final three full reproductions under the reviewed deployed domain policy are saved in `artifacts/rag-enhancements/interest-final-targeted.json`:

| Run | Elapsed | Outcome |
| --- | ---: | --- |
| 1 | 127,809 ms | Recovery timed out; current surcharge rule/applicability still unresolved |
| 2 | 113,513 ms | API answered, but headline says BDT 27,930 while breakdown/conclusion says BDT 42,930 — fails consistency acceptance |
| 3 | 34,373 ms | OpenAI provider error during recovery |

These are **not** three passing tax tests. The second response's plausible breakdown does not establish that every governing treatment was correctly resolved. Earlier salary-only and already-taxable controls passed six repeated calculations under the same original authoritative protocol; that does not certify this expanded example.

The final code additionally rejects reviewer-authored `missing_inputs`; the provider batch above did not rely on that malformed output path. Unit/integration mocks now exercise the actual separate classifier rather than injecting accepted inputs directly.

## Additional corpus and retrieval findings

The local corpus contains an October 2025 ordinance replacing section 163(11), with Sanchaypatra profit in its final-tax table. It also contains the 2026 Finance Act: page 88 replaces section 163, and page 89 supplies the new provisions. The replacement does not repeat every older product name. Product-name searches repeatedly found older text, definitions or worked examples without reliably establishing the replacement's effect.

Both sources have active immutable metadata and modification relationships to the base Act representations. No source content or metadata was rewritten to force a desired total. These observations concern the project's indexed documents, not independent certification of current tax law.

Local and live snapshots differ: local source generation 22 versus live 37. The original local policy also explicitly permits a salary-only estimate before unknown asset-based surcharge when no other income is supplied; deployed policy is less explicit about that scope. Temporary local tests replayed the inspected deployed policy and restored the original policy by compare-and-set immutable revision.

## Withdrawn experiments and remaining work

Broader planner allocation, modified routine coverage prompts, compound requirement IDs, revised discovery hints, a longer compound deadline and an extra provision-heading search were evaluated and withdrawn. They did not deliver reliable expanded-case acceptance, and some earlier variants affected salary retrieval or arithmetic. Their code and failing artifacts are retained under ignored `artifacts/interest-before-fix/compound-experiment/`. They are not part of the retained runtime patch.

Remaining acceptance work:

1. Reliably retrieve and reconcile governing inclusion/final-tax treatment across amendment chains for every supplied income component.
2. Distinguish a requested scoped estimate from a fully settled liability without silently omitting supplied components or material conditions.
3. Validate generated numerical consistency, including agreement between headline, calculation rows and conclusion.
4. Repeat expanded-case and salary controls against the final implementation and the deployed corpus; count unsupported assumptions and contradictory totals as failures.

The local Operator Console session expired during final browser verification. Production-composition API runs and saved traces were available; the final browser check was not completed.

## Rollout implications

The retained runtime correction needs **no migration, reprocessing, re-embedding, index rebuild or new environment variable**. It requires the normal backend deployment and process restart. It does not resolve all mixed-income acceptance failures above.

The evaluator adds `gross_with_interest` with the user's exact question. Its optional `--domain-instructions-file` replays reviewed policy through normal immutable local revisions and restores the original policy with compare-and-set protection. No live configuration was changed.
