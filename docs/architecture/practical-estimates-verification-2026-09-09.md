# Practical estimates and deployment verification — 9 September 2026

## Verified deployment and policy

The live Operator Console test conversation `f8ac4fba-ca13-4eef-8dae-c19aa609e40d` reports `v16-authoritative-conditional`, confirming the user-deployed correction is running. It used live source generation 37 and the then-current policy revision 8. The model failed before evidence review; the UI incorrectly labelled the result a valid evidence refusal.

The practical-estimate policy in [practical-tax-estimates-policy.txt](practical-tax-estimates-policy.txt) was subsequently appended through the normal Operator Console revision workflow and activation was verified:

- Live Income-Tax: revision **9**, revision prefix `c9222c03`.
- Local Income Tax & Budget: revision **36**, revision prefix `00c5eaa2`.

Existing conversations retain their captured policy; use a new conversation to test these instructions. No source document, index, relevance threshold, tax rate or model was changed.

## Intended practical behavior

For an unspecified TDS amount or rate, give a supported provisional calculation before TDS credits. When useful, choose an explicitly labelled ordinary category or gross-interest scenario supported by the available rules. State which certificate, deduction amount, gross/net detail or other personal fact would refine the result. Estimates with multiple income sources may be scoped before unprovided asset-based adjustments, clearly labelled.

An assumption about the user's circumstances is not a new statutory rule. Do not silently omit supplied income, invent a TDS rate, claim assumed deductions were actually paid, or treat final/minimum source tax as an ordinary payment credit without establishing that legal treatment. A specific unresolved legal rule still limits what can be claimed, but missing personal details should not become a blanket prerequisite for useful work.

## Verification blocker

The local four-case smoke run (`interest_unknown_tds`, the original salary-plus-interest question, gross salary, and already-taxable income) stopped at provider review. OpenAI returned HTTP 429 with `credit_balance_exhausted` / `insufficient_quota` and explicitly reported no API credits remaining. Cohere retrieval completed. No generated answer from this batch is an acceptance pass. The live test independently showed an OpenAI provider error; its UI did not expose the upstream billing code.

End-to-end answer quality with the newly activated policy remains **unverified until provider access is restored**. Do not treat successful policy activation or offline tests as proof of a correct tax calculation. No further local provider-connected tests were run after identifying the billing blocker.

## Error-handling correction in the working tree

- Classify exhausted account quota separately from transient rate limits, without retrying billing failures or exposing raw upstream content to clients.
- Carry operational failures from evidence recovery to the normal chat failure path. Both regular and streaming calls persist a failed execution, with no insufficient-evidence reason, and return a service error instead of a tax-rule refusal. A preparation failure never enters answer generation or web fallback.
- Return the stable application error code `llm_provider_quota_exhausted` with an actionable billing message. Other provider failures retain the established service-unavailable contract.
- The evaluator records application error codes and stops its batch after quota exhaustion, restoring any temporary policy in its existing `finally` block.

Verification: **1,139 unit and architecture tests passed**; Ruff lint and Mypy checks passed. New tests cover billing versus transient 429 classification, regular and streaming recovery failures, failed-execution persistence, and suppression of answer-generation progress after a failed review.

These error-handling code changes are newer than the user's verified live deployment and require the normal backend deployment/restart. The project policy changes are already active and require no code deployment. Neither change needs document reprocessing, re-embedding, index rebuilding, migrations or new environment variables.
