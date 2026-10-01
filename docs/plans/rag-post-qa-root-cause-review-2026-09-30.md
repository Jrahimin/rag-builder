# Post-QA root-cause review and guided repair proposal

Date: 2026-09-30. Scope: the current dirty working tree, the twelve saved regular Message API responses, the earlier two repair rounds, and their recorded verification. **Review only: no application, test, configuration, source-metadata, or index changes were made.** This document is the proposed work, not a claim that it has been implemented.

## 1. Conclusion

The refinement added useful infrastructure and stricter validation, but several producers and consumers disagree about their contracts. Retrieved evidence can be sufficient while the answer is still discarded. A larger or newly validated index cannot repair those disagreements.

The strongest demonstrated causes are:

1. **Answer generation is not reliably producing the structured draft that finalization now requires.** The prompt combines incompatible JSON/prose citation instructions; the provider request does not enable JSON output for answer generation. Five saved requests reach the same structured-draft rejection path.
2. **The conditional-evidence prompt shows an object where the schema requires a list.** Both DNCC requests hit precisely that validation error and then exhaust the remaining recovery time correcting it.
3. **A rate quotation cannot inherit already-validated applicability proof from its declared dependency.** The private threshold response contains the heading and the row, but the guard downgrades the row because the period is not repeated inside that row's quotation.
4. **Recovery and the request use different deadlines.** Recovery is allowed to consume the entire actual 50-second work window, leaving no generation/verification reserve. The salary calculation trace demonstrates this.

There are additional temporal-selection, requirement-scope, and diagnostic defects described below. Source-governance gaps also remain, but they do not explain every failure. In particular, the AGM responses already contain complete reviewed proof for both requested facts.

Do not address these results by activating the preview, raising candidate limits, relaxing verification, or changing the generation model first. Fix and verify the broken interfaces before tuning retrieval quality or latency.

## 2. Evidence and what the previous QA establishes

Primary evidence:

- [Broad QA report](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/report.md).
- [Raw-response manifest](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/manifest.json); twelve response bodies and matching Network metadata are in that directory.
- [Structured review evidence](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/root-cause-review-evidence.json), containing per-case diagnostic extracts, non-writing probe outcomes, and source-hash verification.
- [Previous final verification](../../artifacts/cursor-runs/rag-refinement-2026-09-30/final-verification/summary.md) and [acceptance](../../artifacts/cursor-runs/rag-refinement-2026-09-30/final-verification/acceptance.md).
- [Original implementation guide](rag-end-to-end-evidence-refinement-2026-09-30.md), [ledger](rag-end-to-end-evidence-refinement-2026-09-30-ledger.md), and earlier review/repair handoffs.

The reviewed checkout remains at HEAD dbee1df7003f479343ecfb2c44f114e5c92407db with tracked binary-diff SHA-256 29a84a8bf15680f75b29b87744b887acfbb991449277d033a077dc4590954da7. This matches the final repair handoff. The working diff and relevant untracked implementation, rather than HEAD alone, were reviewed.

The private preview is c75f7019-f94b-4435-9036-e9cf59ae216b; the active index is 8eb1595a-8e38-41e9-96c5-0ba419e79bee. Both use source generation 35 in the captured responses. The private build remained unactivated.

### Important corrections to interpretation

- The active-versus-preview comparison is an **index comparison under the same updated backend**, not a controlled before-versus-after code benchmark. It proves no observed output improvement for those paired prompts; it does not prove that every code change had zero benefit.
- Zero factual outputs across twelve responses is an observed result, not a 0% correctness score. The cryptocurrency question is a negative/unsupported-evidence check where abstention may be correct. Historical evidence sufficiency was not independently certified either.
- HTTP 200 confirms successful message delivery, including persisted refusals. It does not mean the requested answer was successfully established.
- Evidence admission, requirement completion, textual claim support, and governing authority are different states. A high reranker score is not proof of any of the latter three.
- A global retrieval-expansion status of unresolved_relationships does not mean every selected claim is blocked. AGM and current-threshold cases demonstrate that reviewed coverage can be complete despite that global diagnostic.
- A null exact_as_of does not prove that explicit AY scope was lost. The historical response contains the correct typed requested_periods value; an assessment year should not be replaced with an invented timestamp.

### Per-case diagnosis

| Capture | Observed terminal behavior | Review conclusion |
|---|---|---|
| [001: explicit threshold, preview](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/001-current-threshold-explicit-response.json) | structured_draft_required; partial coverage | Format failure plus a demonstrated heading/rate proof-composition gap. |
| [02: historical threshold](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/02-historical_threshold-response.json) | unresolved_authority; later-period schedules in reviewed evidence | Requested AY survived normalization. Actual historical-source sufficiency remains open; typed-period selection contains a separate confirmed defect. |
| [03: yearless threshold](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/03-threshold_yearless-response.json) | structured_draft_required despite complete coverage | Failure after reviewed evidence becomes available. |
| [04: salary calculation](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/04-salary_tax_calculation-response.json) | request_deadline_exceeded at 49.994 seconds server processing | Recovery consumes the request work window; coverage is cancelled before answer generation. |
| [05: AGM, preview](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/05-companies_act_agm-response.json) | structured_draft_required despite complete R1/R2 proof | Best positive acceptance seed: retrieval/coverage succeeded, draft protocol failed. |
| [06: DNCC, preview](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/06-dncc_new_trade_license-response.json) | unresolved_authority/recovery deadline | Coverage schema error at checks.0.condition_facets; correction cancelled. Do not report this as demonstrated missing law. |
| [07: rent withholding](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/07-office_rent_withholding-response.json) | incomplete coverage; action_limit_reached | Unnecessary personal-eligibility requirement consumes effort; operative rate/amendment proof also remains unresolved. |
| [08: partnership](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/08-partnership_registration-response.json) | claim_verification_failed; failed_claim_reasons contains unverified | Retrieval admitted eleven chunks. Original generated assertions and verifier payload are absent, so the exact semantic failure cannot be diagnosed conclusively from this capture. |
| [09: cryptocurrency negative](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/09-unsupported_negative-response.json) | abstention; unchanged_scope_and_evidence | Do not turn a correct refusal into a required positive answer. Scheduler identity is still worth correcting. |
| [10: explicit threshold, active](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/10-current_threshold_explicit-response.json) | structured_draft_required; complete coverage | A larger selected heading-plus-row quotation avoids the preview's composition gap, but the shared generation defect remains. |
| [11: AGM, active](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/11-companies_act_agm-response.json) | structured_draft_required; complete coverage | Same downstream defect as preview. Activating the preview cannot fix it. |
| [12: DNCC, active](../../artifacts/cursor-runs/operator-console-qa-2026-09-30/12-dncc_new_trade_license-response.json) | recovery deadline; condition_facets list_type for two checks | Same conditional-proof protocol defect as preview. |

## 3. Findings and causal chains

### F1 — P1: structured generation is required at the consumer but not guaranteed at the producer

**Confirmed code:** [PromptBuilder](../../backend/app/modules/conversations/prompt_builder.py), lines 127–147, requests a JSON answer draft and says not to include citation markers. Later instructions at lines 234–253 require exact inline citation markers, followed by the registered prose-oriented final instructions. [Prompt registry](../../backend/app/modules/conversations/prompts/registry.py), lines 33–65 and 71–95, reinforces factual sentences, tables, and inline citations. The draft instruction shows a segment array without a complete, valid top-level object example.

[ChatService](../../backend/app/modules/conversations/services/chat_service.py), lines 429–433, calls the ordinary generate interface. [OpenAI-compatible adapter](../../backend/app/platform/providers/implementations/openai_compatible_chat.py), lines 103–112 and 286–296, enables JSON-object mode only for listed review purposes; answer_generation is absent. The provider-neutral [LLM contract](../../backend/app/platform/providers/contracts/llm.py) has no structured-output contract parameter.

Finalization at ChatService lines 3855–3904 requires an object with segments whenever selected chunks have reviewed_proof. Plain prose, or a bare segment array, is discarded with structured_draft_required. Lines 1957–1958 then set claim_verification_failed. That pre-existing failure reason prevents the reviewed-coverage fallback at lines 2032–2046 from running.

**Runtime evidence:** five responses record structured_draft_required: preview explicit/current/AGM and active explicit/AGM. The parser branch establishes a shape mismatch after its limited fence handling; the exact original model draft was not saved, so it is not possible to distinguish prose from a bare array in those captures.

**Non-writing reproduction:** actual PromptBuilder output contains the JSON/no-citation instructions followed by the inline-marker and final prose instructions. The actual renderer rejects both plain prose and a bare segment array; the equivalent object with a segments key renders correctly. Actual adapter body construction produces JSON mode for recovery_planning but not answer_generation or claim_verification. No provider request was made.

**Proposal:** introduce one internal AnswerDraft schema and one generation mode chosen explicitly from the prepared evidence contract. Generate the provider schema and prompt examples from that model. When a draft is required, suppress the competing Markdown/citation-output directions; the renderer owns visible citation numbering. Add capability-aware structured output to the existing provider abstraction instead of embedding vendor-specific parameters in ChatService. Allow at most one budgeted shape correction using the same immutable proof set. Do not treat invalid shape as evidence absence, and do not weaken factual proof checks to accept arbitrary prose.

For supported fallback, render only already-approved atomic assertions and their complete proof dependencies, then apply the same semantic and deterministic guards. A coverage summary is not automatically a verified final answer. Preserve a draft format/version and reason code in diagnostics, with raw drafts retained only in an authorized protected debug artifact.

### F2 — P1: conditional-facet output shape is inconsistent with the validation schema

**Confirmed code:** [authoritative compatibility prompt](../../backend/app/modules/conversations/prompts/authoritative_compatibility.py), lines 379–387, and [general repair prompt](../../backend/app/modules/conversations/prompts/evidence_repair.py), lines 142–150, show condition_facets as an object. [Coverage schema](../../backend/app/modules/conversations/services/evidence_coverage.py), line 100, requires a list of ConditionFacet objects.

**Runtime evidence:** DNCC 06 has checks.0.condition_facets/list_type; DNCC 12 has that error for checks 0 and 1. The correction calls have only 6.250 and 8.672 seconds left and are cancelled after approximately 6.232 and 8.690 seconds. Both builds fail at this same interface.

**Non-writing reproduction:** passing the prompt-shaped object to the actual CoverageVerdict model reproduces list_type. Passing a valid list of the same condition object succeeds. A transient test fixture with when=null also correctly failed because that field is a string; the corrected fixture used an empty string. This fixture correction is not an additional production finding.

**Proposal:** derive every planning, coverage, delta, and correction example from one schema. Show condition_facets as an array, with zero, one, and multiple-condition examples and explicit allowed field types. Require source-bound evidence indexes. Use constrained output where supported, while keeping quotation and semantic checks after schema validation. Test actual composed prompts and provider bodies, not just manually well-formed schema inputs.

### F3 — P2: proof validation ignores validated requirement dependencies

**Confirmed code:** [evidence repair](../../backend/app/modules/conversations/services/evidence_repair_service.py), lines 445–475 and 509–544. The guard gathers only each check's own quotations, then demands the requested period in that string. It does not compose proof using EvidenceRequirement.depends_on.

**Runtime evidence:** preview 001 has R1 with the applicable year/category in chunk 5aa3a215-d3da-4242-b16f-74df447729c4, lines 1–7. R2 quotes the rate table in lines 8–14 of the same chunk and depends on R1. R2 becomes partial with requested period 2026-27 and needs_adjacent_context even though the heading was already reviewed. Active 10 uses a larger heading-plus-row selection and becomes complete.

**Non-writing reproduction:** identical fixture text with a validated heading in R1 and a rate row in dependent R2 fails; combining the same text in R2 passes. A wrong-period fixture still fails. Selection boundaries, rather than a real evidence difference, change completion.

**Proposal:** validate each assertion against an explicit composed proof containing its own quotation and validated applicability dependencies. Require matching project, source edition, index/source generation, provision/table identity, category, and requested period. Allow an attested governing heading to apply to its rows; never borrow a nearby heading solely because it is adjacent. Keep conflicting or ambiguous headings unresolved. Preserve all contributing spans in citations and reuse this proof through generation and verification.

### F4 — P1: the local recovery budget consumes the actual request deadline

**Confirmed code:** [ChatService](../../backend/app/modules/conversations/services/chat_service.py), lines 293 and 511, caps work at start + 50 seconds. Line 1108 independently sets evidence_deadline to start + 60. Lines 1427–1429 subtract ten seconds from that second deadline. Recovery can therefore run until the real 50-second deadline. The separate ten seconds after that are available for deterministic terminal persistence, not for completing a normal answer.

**Runtime evidence:** salary 04 begins applicability work at 16.131 seconds. Planning uses 10.710 seconds. Parallel recovery retrieval ends around 35.9 seconds, and coverage starts at 35.958. Coverage is cancelled at 49.990, with no generation or claim-verification stage. These span durations overlap and must not be added as independent serial costs.

**Proposal:** use one authoritative monotonic request deadline. Derive a recovery deadline by subtracting explicit generation, verification, and persistence reserves from it. Pass the same deadline object to scheduling, provider calls, semaphore waits, structural completion, web review, and finalization. Before admitting an action, check that the action and its mandatory subsequent validation fit. Preserve completed independent proof on timeout, and return a verified partial result only when that partial result actually exists. Do not lengthen the deadline as a substitute for repairing the accounting.

### F5 — P2: adjacent legal-year labels overlap numerically and can influence replacement selection

**Confirmed static defect:** [source metadata read](../../backend/app/modules/knowledge/source_metadata_read.py), lines 568–581, matches typed AY/FY facts using inclusive overlap of start_year/end_year. AY 2026–27 consequently overlaps AY 2025–26 at 2026, although they are distinct labeled assessment periods. The same interval predicate is used for applicable replacements at lines 611–635.

**Impact:** a later-period source can enter historical recall, and a replacing source that matches only through this overlap can potentially suppress the requested historical source.

**Evidence boundary:** historical 02 contains the correct requested AY, later-period reviewed evidence, and three source_replaced exclusions. The saved response does not include all source scope_facts needed to attribute that refusal specifically to this predicate. Do not claim that the historical document is absent, or that this defect alone caused that case.

**Proposal:** distinguish a legal period identifier from an effective-date interval. Match a single typed AY/FY by identity; represent multiple covered periods as explicit members or a correctly typed period range. Preserve calendar effective intervals and known-at publication cutoffs separately. Apply identical semantics to initial eligibility, replacement, amendment expansion, recovery, and pinned replay. Verify historical editions with actual source attestations before changing lifecycle or replacement metadata.

### F6 — P2: task scope still expands stipulated rule lookups into personal eligibility

**Runtime evidence:** rent 07 asks what a specified person must deduct. The plan adds a prerequisite to identify which payer qualifies, searches that branch, and ends with gaps including whether the payer qualifies and all categories in the statutory definition. That is a different task from stating a conditional rule for the stipulated category. Rate/amendment proof is also missing, so this is a contributing defect, not a claim that removing R2 alone would make the answer correct.

**Code:** [RequestScope normalization](../../backend/app/modules/conversations/turn_resolution.py), lines 791–879, has category and jurisdiction fields, but fills them only from metadata filters. The natural-language questions still produce null values. explicit_inputs is parameter-like text rather than a typed record of stipulated conditions. [Repair requirement handling](../../backend/app/modules/conversations/services/evidence_repair_service.py), around line 387, trusts the planner's requirement origin rather than reconciling it against supplied facts.

**Proposal:** extend the existing normalized scope with typed stipulated facts and their origin, keeping scenario assumptions distinct from legal evidence. Validate the requirement graph against the user's requested task. A rule lookup may explain a conditional definition without proving the user's personal eligibility; a personal eligibility question requires that proof. Do not infer category from a filename or treat project defaults as evidence of law. Keep only genuinely necessary dependencies on the critical path.

### F7 — P2: final status and diagnostics obscure the actual failure

Several independent observations have direct code causes:

- Draft failures advertise an insufficient-evidence notice even when coverage is complete. ChatService lines 2238–2256 do not classify answer-draft schema failure separately.
- [Evidence funnel](../../backend/app/modules/conversations/services/chat_service.py), lines 3196–3205, selects answered from generation_ran. AGM and partnership responses consequently report answered with zero supported claims and a withheld answer.
- Failure finalization at lines 2048–2066 clears original claims. Partnership preserves only failed_claim_reasons=[unverified], preventing a precise distinction between semantic rejection, malformed verifier output, and verifier unavailability.
- [Claim entailment](../../backend/app/modules/conversations/services/claim_entailment_service.py), lines 35–57, converts several provider/parse/schema problems into all-unverified verdicts. A completed provider call alone does not establish valid verifier output.
- [Verification notice](../../backend/app/modules/conversations/notices.py), line 182, defaults failure_stage to coverage_review even when called for post-generation verification failure.
- Deadline finalization writes request_work at ChatService line 568; [Test Lab](../../frontend/src/features/lab/TestLab.tsx), lines 2935–2937 and 3090–3094, reads lifecycle. The most useful timeout trace is therefore not shown in the normal processing-work inspector.
- [Selector correction](../../backend/app/modules/conversations/services/evidence_repair_service.py), line 1789, is marked completed before the correction is attempted. Both DNCC traces call it completed although the provider stage is cancelled.

**Proposal:** compute one typed terminal outcome after finalization and use it for content, notices, finish reason, response diagnostics, SSE final event, persisted GET, and UI badges. Keep successful HTTP delivery compatible with a failed answer outcome. Preserve sanitized stage/reason/error paths and counts for rejected draft claims separately from public supported claims. Use one lifecycle field across normal and timeout paths, with an explicit persistence-completed indicator. Record action completion only after validated results, and represent cancellation distinctly.

### F8 — P2: recovery repetition and source-governance limits require targeted follow-up

[Recovery fingerprint admission](../../backend/app/modules/conversations/services/evidence_repair_service.py), around line 2524, uses the query text as the fingerprint, while [RecoverySchedule](../../backend/app/modules/conversations/services/recovery_schedule.py), line 25, labels repeats unchanged_scope_and_evidence. The same query can target changed anchors or evidence. Include normalized scope, unresolved requirement IDs, build/source generations, and anchor/evidence hashes, while retaining hard finite limits. Case 09 exposes the stop reason, but as a negative case it does not prove a lost answer.

The captures also contain modifier relationships with unknown provision_effect, empty supporting_spans and target_provisions, and incomplete metadata. Adding schema fields or rebuilding chunks does not populate legal authority facts. Inspect the relevant operative source passages and produce reviewable, source-backed relationship corrections. Keep unverified extraction candidates distinct from validated facts; do not automatically activate draft sources or mark a whole source effective to make tests pass. Source corrections require their own later, explicit implementation scope.

## 4. What has actually improved, and what remains open

| Area | Evidence-backed disposition |
|---|---|
| Internal provider temperature failure | The earlier hardcoded temperature was removed from ClaimEntailmentService. Current QA reaches later stages rather than reproducing those HTTP 400 errors. |
| Private structural build isolation and identity | Recorded job succeeded, preview validated, active pointer preserved. Structural intent/membership checks exist. This is operational progress, not proof of answerability. |
| Historical scope transport | AY 2025–26 survives in normalized requested_periods. Selection semantics still have the adjacent-period issue. |
| Recovery bounds | Separate structural actions and finite limits exist. Zero focused semantic follow-ups does not mean structural completion is unavailable. Scheduling order, identities and completion telemetry still need work. |
| Usable retrieved proof | AGM has complete source-bound proof on both indexes; current/explicit thresholds also reach reviewed proof. Downstream integration prevents output. |
| Frontend quality | The recorded final frontend gate passed: 112 tests and production build, after permissions for generated output. Not rerun during this review. |
| Backend quality | Recorded final gate still has 11 lint findings, two mypy errors, 88 unit/architecture failures, and one historical integration failure. These are unresolved recorded results, not new test results from this review. |
| Evidence Quality UI | No versioned dataset/run existed during QA, so the page offered no benchmark evidence. |
| Complete answer journey | Not accepted. No successful positive matrix, complete repeated comparisons, or current SSE parity evidence was demonstrated. |

The original plan expected adjacent positive/negative journey tests and successful repeated seed answers. The implementation has focused helper tests, including a structured renderer fed manually valid JSON, but those do not exercise the real composed prompt and provider-output contract. The condition-facet test similarly supplies the valid array that the live prompt fails to request clearly. The earlier final acceptance already declared the work incomplete; the broad QA exposes the concrete gaps behind that status.

## 5. Guided implementation sequence for a later authorized repair

Each phase should be a small reviewable change with its own positive and negative acceptance cases. This proposal does not authorize implementation now.

### Phase A — freeze the evidence and repair the two protocol mismatches

1. Preserve the existing raw captures. Add compact deterministic fixtures derived from AGM and DNCC evidence; exclude credentials and avoid requiring live provider calls for every test.
2. Define canonical internal models for AnswerDraft and condition facets. Generate exact JSON examples/schema from them.
3. Make the prepared turn explicitly choose a structured factual draft or a deterministic nonfactual terminal response. Keep that choice consistent in Regular and SSE.
4. Propagate output-schema intent through the provider-neutral interface and wrapper layers. Feature-detect capability; test JSON-only and unsupported-schema fallbacks. Do not hardcode this behavior to one model name.
5. Remove competing citation/prose output instructions in draft mode. Render visible citations from approved proof references.
6. Make a bounded correction preserve evidence IDs and assertion scope; never permit it to invent new proof.

**Exit:** the real prompt/body/parser chain produces a verified AGM answer and a valid DNCC conditional review in controlled tests. Malformed output is classified as a protocol failure; foreign or mismatched proof references remain rejected.

### Phase B — compose proof and enforce the intended task

1. Build on the existing EvidenceRequirement dependency graph and proof map. Resolve applicability dependencies before declaring a central rule unsupported.
2. Carry the heading, numeric row, conditions, exceptions and amendment proof as one claim-level bundle without losing original spans.
3. Populate typed user stipulations and requested task once. Reconcile planner requirements against that contract.
4. Reuse already-reviewed assertions only under the exact proof/scope identity. Semantically verify genuinely new or changed assertions; do not use embeddings as proof.

**Exit:** split and combined selectors over the same threshold text produce equivalent completion; wrong heading/year/category cases fail. A specified-person rule lookup does not demand personal identity, while a personal eligibility question still does.

### Phase C — correct typed temporal selection and audit the actual authority gaps

1. Correct AY/FY identity matching and use the same predicate for replacement eligibility.
2. Add historical, current, multi-period, retrospective, and known-before/available-on integration fixtures using pinned source generations.
3. Inspect the historical and rent corpus passages through read-only tooling to decide which required facts truly exist. Export the source IDs, editions, exact passages and relationship evidence for review.
4. If source facts need correction, propose a separate narrow metadata/data migration with rollback. Rebuild only where text/index representations actually change; do not repeat a whole rebuild for a prompt defect.

**Exit:** historical evidence selection is reproducible and respects typed periods. Unresolved real amendments remain explicit limitations scoped to affected claims, without blocking unrelated proven facts.

### Phase D — fix deadline ownership and bounded execution

1. Derive all phase deadlines from one monotonic request deadline and account for normal answer generation, verification and persistence separately.
2. Batch compatible retrieval work, consolidate changed evidence before the final delta review, and reserve budget for that review before starting search.
3. Deduplicate against real scope/evidence state, not query wording alone. Finalize action status only after completion or cancellation.
4. Skip semantic claim verification for known deterministic nonfactual failure templates. The DNCC preview spends about 2.3 seconds on an LLM claim-verification call after recovery has already failed.
5. Profile long-query keyword SQL and provider context size only after correctness is restored. Current evidence does not justify attributing every latency problem to SQL.

**Exit:** delayed/failing planner, search, schema correction and verifier fixtures remain bounded and leave the intended reserve. Completed proof is retained for a valid partial result, or the terminal reason accurately identifies the exhausted stage.

### Phase E — refine public messaging and operator diagnostics together

Use the response model below. Update the matching conversation API, feature documentation, generated client types and UI tests as one change. Add an operator-only diagnostic view for failed assertions/schema paths, with explicit access and retention controls; keep unverified factual drafts out of normal answer content.

### Phase F — establish acceptance before activation

1. Resolve or explicitly triage every recorded backend gate failure against the frozen baseline; do not silently suppress assertions or regenerate expectations to match refusals.
2. Create a versioned evaluation dataset with source-grounded answerability labels. Do not label the unsupported negative case as a required positive answer.
3. Run the paired tests below, then repeat every verified answerable live seed three times on the candidate build. Preserve per-run code, configuration, model, source/index identities, request time and actual response body.
4. Verify Regular POST, SSE final, persisted GET and UI using the same terminal outcome contract, including timeout and malformed-output paths.
5. Report answer correctness, proof completeness, safe abstention, latency and protocol reliability separately. Measure p50/p95 only with adequate repeated warm/cold observations and recorded load.
6. Run the repository quality gate and any separately required delivery checks. A validated index or green frontend build is not the end-to-end release gate.

Keep the existing active build unchanged until positive answerability and negative safety gates pass. Activation is a separate later action.

## 6. Proposed message-response contract and wording

Preserve the existing response envelope and public verified claims/citations. Add an explicit terminal result describing the answer outcome rather than inferring it from whether generation ran. Suggested fields:

    outcome: answered | partial | needs_input | insufficient_evidence |
             unresolved_authority | verification_failed | timed_out
    reason_code: stable, specific diagnostic code
    failure_stage: retrieval | coverage | draft_schema | claim_verification | persistence
    requested_scope: normalized period, category and source restriction
    coverage: complete | partial | incomplete | not_assessed
    retryable: whether an unchanged user request can meaningfully be retried
    next_action: retry | supply_input | review_source | contact_operator | none
    supported_requirement_ids / unresolved_requirement_ids
    lifecycle: consistent timing and action diagnostics for every terminal path

Do not include secrets, hidden reasoning, or an unverified draft in this public contract. Keep diagnostic error types and schema paths bounded and sanitized. Preserve draft/claim candidate counts separately from published supported-claim counts, so zero published claims does not erase how verification failed.

| Actual state | Proposed user-facing response | Operator explanation |
|---|---|---|
| Evidence complete; answer draft malformed | “I found relevant source passages, but could not complete a verified answer because an internal response check failed. You do not need to change your question.” | verification_failed / answer_draft_invalid / draft_schema; schema version and error path. Offer retry only if policy allows it. |
| Coverage output malformed, as in DNCC | “The source review could not be completed because of an internal validation problem. This does not show that the requested information is missing.” | verification_failed / coverage_schema_invalid; condition_facets list_type; correction attempted/cancelled. |
| Genuine unresolved amendment | “I found the relevant rule, but could not establish how the amendment affects the provision for the period you asked about.” | unresolved_authority; affected provision, source IDs and missing supporting spans. Include independently verified unaffected facts where available. |
| Historical schedule not established | “The reviewed passages did not establish the threshold for AY 2025–26. They cover later assessment years.” | insufficient_evidence / requested_period_not_established; distinguish selected-evidence absence from whole-corpus absence. |
| Request budget exhausted | “I could not finish verifying the requested calculation within the time limit. No final tax amount has been certified.” | timed_out; actual stage and remaining/completed proof. Do not imply that the user's question was too broad when scheduling caused the failure. |
| Some requirements verified | Lead with the central question's status, then show verified independent facts and one concise statement of the remaining gap. | partial; claim-level proof links and exact unresolved requirement IDs. Never present an unsupported subtotal as a final liability. |
| Missing personal input | Ask only for the fact that selects between already-evidenced branches; provide supported conditional branches where useful. | needs_input with the missing scenario field. Do not call missing personal data missing law. |
| Unsupported negative query | “The reviewed evidence does not establish a specific rule for that activity and period.” | insufficient_evidence; safe abstention. Do not invent a rate to improve a positive-answer metric. |

These are proposed templates, not legal conclusions or automatically reusable answers. Produce them from the typed terminal result, not free-form guesses about why the pipeline stopped. Localize the templates using the resolved language. The UI should show “Verification failed,” “Timed out,” “Needs source review,” or “Supported answer” consistently with the API, rather than treating every refusal as a retrieval failure.

## 7. Acceptance matrix

| Change | Positive case | Paired negative / resilience case | Required observation |
|---|---|---|---|
| Structured draft | AGM with the captured two reviewed requirements | Plain prose, bare array, foreign proof ID, extra unsupported duty | Valid contract produces verified citations; malformed shape has its own reason and never reaches a false answered state. |
| Conditions | DNCC tenant/factory checklist with one/multiple facets | Object instead of list; mismatched evidence index; owner treated as tenant | Prompt and schema agree; category conditions remain attached to their duties. |
| Dependency proof | Threshold heading and rate split across dependent selectors | Heading from different period/table/edition | Same-source split/composed proof is equivalent; unrelated proof cannot authorize a rate. |
| Task scope | Rule applicable to a stipulated specified person | “Do I qualify as a specified person?” | Only the second requires personal-eligibility proof. |
| Legal periods | Historical AY 2025–26 plus next-year distractor | Adjacent-year replacement; retrospective publication before/after cutoff | Typed period and known-at policies are preserved in initial and recovery queries. |
| Shared budget | Slow planning/retrieval followed by valid evidence | Coverage timeout, cancellation, exhausted correction budget | Normal finalization reserve survives; terminal diagnostics retain identities and actual stage. |
| Semantic verification | Faithful translation/paraphrase and authorized arithmetic | Wrong year/category/negation/extra claim; malformed verifier output | Exact proof reuse is safe, additions are verified, and provider/parse failure differs from unsupported assertion. |
| Messaging/transport | Regular, SSE final, persisted GET and UI for one verified answer | Draft failure, DNCC schema failure, timeout, genuine source gap | Same content/outcome/claims/citations; lifecycle visible on timeout too. |
| Evaluation | Independently labeled answerable sources | Truly unsupported query and missing historical evidence | Completion and correct abstention scored separately; no fabricated facts rewarded. |

For each verified answerable seed, require three controlled successful repetitions before claiming it fixed. For latent defects such as the adjacent-period SQL predicate, require an integration reproduction even if the live historical corpus cannot yet supply a positive answer. Success criteria must test the production path, not just a helper supplied with ideal model output.

## 8. Review execution and limitations

The parent inspected updated generation, proof verification, provider transport, terminal status, UI, and saved runtime diagnostics. One fresh independent dev-reviewer used Astra Medium as previously requested, emphasizing recovery, authority, temporal selection and task scope. The effective environment was workspace-write with a non-mutating review boundary, not enforced read-only sandboxing. No source edits, database operations, live provider requests, browser mutations, or cache-producing test suites were performed for this review.

Non-writing checks used Python -B to exercise actual prompt/renderer/schema/provider-body code and the dependency-period guard. The independent reviewer also parsed relevant modules and exercised the isolated period helper. The independent review's before/after diff fingerprint and untracked inventory matched. The parent rechecked the raw-byte diff fingerprint and original untracked implementation hashes after creating only this proposal/evidence artifacts.

The final source check matched all 73 hashes in the repair2 handoff, with zero mismatches; the tracked binary-diff hash also remained unchanged. All report links were checked. The previous full quality results were read, not rerun. Raw model drafts and verifier inputs/outputs were not captured by the broad QA. Live corpus scope facts, complete source documents and runtime-loaded code attestation were not independently refreshed. The current checkout matches the frozen handoff and the response behavior matches the inspected branches, but that is not a cryptographic attestation of the running service.

The exact historical-source and rent-amendment sufficiency questions therefore remain open. The proposal identifies how to resolve them without guessing source metadata or weakening authority rules. The four principal interface/budget defects above are supported directly by current code, response diagnostics and non-writing reproductions.
