# Live smoke review — 9 September 2026

Scope: two fresh Operator Console conversations, one attempt each, using the live Income-Tax project, policy revision 9 and Luna. No application code, project settings, models, source metadata or indexes were changed. This is a diagnostic smoke check, not tax-law certification or a benchmark distribution.

## Results

| Scenario | Conversation | Browser round trip | Outcome | LLM calls | LLM input + output tokens |
|---|---|---:|---|---:|---:|
| Original: salary BDT 1,200,000, investment BDT 60,000, Sanchaypatra interest BDT 24,000, bank interest BDT 2,200, male under 40, Chittagong | `d2959e94-53f2-4cd8-a0c4-25abc5681f11` | 99.262 s | Refused; coverage incomplete | 6 | 163,389 |
| Salary and investment only; explicitly request estimate before unavailable TDS credits and asset surcharge | `e7b621cc-6183-4d39-a10f-06923d0d2907` | 52.066 s | Provisional answer; grounding review incomplete | 3 | 53,497 |

All observed LLM calls completed. The previous provider/billing error was not reproduced. Consequently this run does not exercise or certify the new provider-error response path. Recovery reports `v16-authoritative-conditional`; policy revision 9 is active. Source generation 37 was visible in the original-question diagnostics.

The salary-only answer produced BDT 39,000 before TDS and asset surcharge, with gross-salary and ordinary private-sector resident assumptions. Its arithmetic is internally consistent: 1,200,000 minus 400,000 equals 800,000; the displayed slab amounts total 45,000; subtracting the displayed 6,000 rebate yields 39,000. This checks arithmetic only, not the legal validity of those rates. The answer explicitly says an omitted surcharge is not necessarily zero and invites TDS/asset details as refinements. The console reports complete evidence coverage but only 13 of 28 claims supported.

## Why the original question still fails

Recovery ran 12 search queries and returned `coverage_incomplete`, rather than `repair_unavailable` or `provider_error`. Its final missing-evidence message concerned the governing inclusion of bank/Sanchaypatra interest in total income and any special/final-tax treatment. Eight of twelve final checks were supported; four were not. Source ranges validated, but overall coverage did not.

The personal-input escape path in the current recovery code requires every source check to pass before classifying remaining personal gaps. It therefore did not apply here. The provisional-answer policy cannot by itself produce a partial answer when the backend blocks generation for legal coverage. The review's initial missing list also mixed gross/net personal uncertainty with legal treatment, so these dimensions need clearer separation.

Authority diagnostics still contain two amendment relationships without provision targets and one `ungoverned_or_incomplete_metadata` relationship. These warrant a focused audit, but this smoke run does not prove that editing those records alone would resolve the answer.

## Remaining work, in priority order

1. **Recover the exact governing interest provisions and amendment chain.** Verify applicable inclusion, final/minimum tax and credit treatment for the chosen assessment year. Link replacement provisions by section, not only product-name similarity. Correct metadata only after checking the underlying documents.
2. **Support a useful partial answer when a separable component remains unresolved.** Return independently supported work with explicit exclusions and pending items, without calling it the complete liability or silently omitting supplied interest. Classify unknown personal facts separately from unestablished law before deciding how much can be answered.
3. **Make claim verification distinguish facts, arithmetic, assumptions and legal assertions.** The salary test flags the user's salary, derived arithmetic and the TDS/asset refinement note alongside legal claims. Review these classifications rather than assuming all 15 flagged claims are legally wrong or weakening legal verification globally. Add arithmetic consistency checks separately.
4. **Reduce repeated recovery work once coverage is reliable.** The original run spent 94.673 s in coverage/recovery, with 13 reranks and 26 embedding calls. Salary-only recovery took 33.981 s before 13.414 s of generation. Repeated evidence review is the main latency/cost target; changing only the answer model would not remove all of it. Stage timings overlap and should not be summed.
5. **Improve refusal presentation.** The UI labels the original refusal “Passed / Valid refusal,” although the user's task remains unanswered. Show the specific unresolved component and distinguish a safely withheld answer from successful task completion.

No tuning was applied: lowering thresholds or broadening assumptions would not establish the missing legal treatment. GPT-5.4 mini is not currently registered as a selectable generation model, and the hosted-managed capability profile permits Luna. A future mini trial needs explicit model registration/allowlisting, its correct prompt budget, and a small comparative tax evaluation; it does not inherently require re-embedding documents.
