# Company compliance timeout investigation — 16 September 2026

## Evidence and timeline

Production project: `f932c9e0-40af-4ff3-8704-ac3e542f2a8c`.
Conversation: `e2d4440d-99ab-42f3-b8f3-811674e14dad`.
Attachment: `5d78142b-4b3d-4b41-a3e1-387ce1db2b8f/pasted-text.txt`.

- First user message `2f49acab-4313-4c96-90c0-e5358bf11f25`: 2026-09-15 21:56:21.479 UTC.
- Cloudflare 524, ray `a3bae1819a98a472`: 21:58:26 UTC. The supplied error reports a 120-second proxy read window.
- Assistant `92ca1e2c-e886-44ee-81e1-89cfce44a6fe`: 21:58:29.045 UTC, 127,537 ms total latency. Work continued after the client timed out.
- Retry user message `36543531-d2ca-4290-8c88-27267732c246`: 22:00:34.590 UTC.
- The attachment contains the first completed answer and the retry question, not a second completed answer. Read-only inspection of the live conversation subsequently found the retry's saved error: “The language model provider is temporarily unavailable.” Its repair diagnostics say `repair_unavailable`, `failure_reason: timeout`, v20. This is the application's evidence-review deadline, not proof of a provider outage.

## Root cause

The first turn spent 118,978 ms in preparation, including 115,375 ms in coverage/recovery, versus 7,995 ms in generation. Nested stage durations overlap and must not be summed. It made seven LLM calls, eleven reranker calls and sixteen embedding calls. Four review calls consumed approximately 52,609–61,412 input tokens each; two structured-response retries repeated expensive review. The final output used 874 tokens. Output truncation or a 2,048-token answer allowance did not cause this incident.

The regular JSON endpoint waits for all work before returning. The console defaulted to that transport. Streaming had progress events but no periodic keep-alive during long gaps. A 120-second recovery budget also leaves no room inside a 120-second synchronous proxy window for retrieval, generation and persistence.

## Answer assessment

The first answer explicitly limits itself to AGM compliance. It does not satisfy the requested legal, regulatory, tax and annual-compliance overview. The review found only one fully supported requirement out of twelve. In particular, it discarded evidence for presenting audited accounts because the same requirement also demanded accounting records and auditor appointment.

The stated AGM timing and section 82 maximum fines agree with the saved official Bangla statutory text: Tk 10,000 and Tk 250 per continuing day after the first day. The Tk 250 statement was nevertheless marked unverified by the numeric verifier; thirteen other unsupported segments largely describe missing evidence. Thus the reported score is not a reliable direct measure of legal accuracy. Current-law completeness is not certified by this comparison. An official Bangladesh Bank copy also supports section 82: https://www.bb.org.bd/feidportal/guidelines/company_act1994.pdf . Fresh Ministry full-page retrieval timed out during this audit.

A first local reproduction, with the deliberately small business corpus, bypassed coverage review (`not_needed`) and generated a much broader answer. It also suggested a conditional section 83 statutory-meeting duty without retrieving subsection 83(12), which exempts private companies under that section. That test is a failure, not a quality improvement: stronger similarity scores do not establish legal applicability.

## Implemented changes

- SSE comments immediately establish the response and recur every fifteen seconds while waiting for an event. Pending work is preserved, and cancellation closes the upstream generator. Responses disable caching/transformation and request no proxy buffering.
- The console defaults to streaming; Regular remains explicitly selectable. The regular JSON API contract is unchanged. API integrations issuing long requests must use `/messages/stream`; these changes do not make the synchronous route immune to a proxy timeout. After an ambiguous timeout, inspect saved messages before submitting the same work again.
- Application review timeouts now return an explicit `evidence_review_timeout` message, not a provider-outage claim.
- Broad authoritative compliance requests trigger completeness review even when initial retrieval is relevant.
- Planning separates independently answerable duties. Partial review may retain a proven duty while explicitly preserving unresolved details as exclusions. Entity exclusions and closing subsections must be checked before asserting applicability.
- For broad recovery, focused dependency heads receive context slots ahead of bulk initial hits, while preserving an existing admitted span for duplicate chunk IDs. Evidence admission, authority scope and exact-proof validation remain enforced.
- Partial-answer instructions group related gaps into a short closing paragraph rather than reproducing the internal missing-requirement checklist.

No production configuration, source lifecycle, tax source or embedding model was changed. No production generation was submitted in this investigation.

## Validation

Targeted checks passed: eight heartbeat/error tests; fifteen overview/partial-scope/initial-proof/budget tests; two focused dependency-budget and narrowed-partial tests; two frontend citation/refusal tests. Ruff and diff checks were used. No integration suite was run.

Final local conversation: `338b2a1c-72f9-4126-9faa-4a92df70ed07`. The original broad question completed through streaming in **157,165 ms**, without losing the HTTP response. Trace confirms v21 and `compliance_overview`, partial answer with five scoped requirements: AGM, annual statement/certification, accounting books, accounts/audit, and selected filing-default consequences. It no longer suggested the private-company statutory-meeting duty. Claims: 28 of 42 supported; answer remains ungrounded overall. Recovery took 117,672 ms, initial retrieval 12,800 ms, generation 25,023 ms. Five LLM calls and one structured retry; review inputs approximately 11–12.6K tokens. This is a transport success and broader partial coverage, **not a speed or accuracy pass**.

The generated retention period was **11 years**, while section 181(5) of the supplied Bangla source says **বার বৎসর (12 years)**. The verifier flagged it unverified. This concrete generation error remains unresolved; do not approve or reuse the answer as a reliable checklist. Additional unverified table fragments and lack-of-evidence caveats also affect the score. Tax/VAT and current RJSC procedures remain outside the deliberately small local corpus. No extra local generations were run after this finding: two end-to-end cases total, the first exposing the bypass and the second verifying review/streaming while documenting the remaining failure.

Remaining work is targeted numerical-generation correction and shorter review execution, followed by a single focused check. Current changes require deployment before production adoption; proxy forwarding of heartbeat frames still needs deployment-level verification. The synchronous JSON endpoint remains vulnerable for requests longer than the proxy window. No automatic replay or idempotency mechanism was added.
