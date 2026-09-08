# RAG enhancement validation — 8 September 2026

## Scope and reproducibility

Validation uses the existing local PostgreSQL/Redis deployment without Docker, the production conversation composition, and the configured OpenAI/Cohere providers. The live Operator Console was inspected read-only; no live rollout was performed.

Tax replays use Project `2ee2756f-ad27-44df-a9d3-1316b10ccbb1`, active index `bc61c185-faef-41e3-93eb-74a80f534c07`, source generation 22, and the original authoritative/translation-disabled policy. The context sweep restored that policy as immutable revision 17. No tax source was rewritten or reindexed. Each replay creates a labelled conversation and records a hash of the backend code used. Raw records are retained under `artifacts/rag-enhancements/`; they are local evaluation artifacts, not source-controlled corpus content.

The archived baseline is the repository HEAD captured before these changes, replayed against the same corpus, policy and providers. First and subsequent turns are reported as process-order cohorts; caches were not evicted and these are **not controlled cold/warm measurements**. Three observations give only descriptive p50/p95 estimates. Total time includes refusals, which cannot count as performance improvements. Provider latency and concurrent machine load affect timing.

## Tax quality and performance

The original three-repeat baseline answered all gross-salary, already-taxable-income and current-rebate cases. It refused all three ambiguous historical AY 2024–25 cases. Gross salary and already-taxable income are deliberately separate inputs: the corpus benchmark produces BDT 39,000 and BDT 104,000 respectively, with the explicitly stated scenario assumptions and current rules.

| Baseline case | Answers | Total p50 | Total p95 | Preparation p50 |
|---|---:|---:|---:|---:|
| Gross salary | 3/3 | 121.9 s | 139.5 s | 102.0 s |
| Already taxable | 3/3 | 86.9 s | 87.5 s | 70.4 s |
| Current rebate | 3/3 | 57.1 s | 63.6 s | 48.4 s |
| Historical AY 2024–25 | 0/3 | 106.5 s | 111.2 s | 106.5 s |

Semantic requirement review was **not accepted for authoritative Projects**. Repeated gross-salary trials introduced missing-rule refusals, despite successful already-taxable and rebate answers. The implementation therefore retains the original v15 authoritative planning/coverage prompts and source presentation order. This is a deliberate staged deviation from the plan's universal semantic requirement migration, prioritizing the user's tax-quality constraint. New factual and multi-perspective reviews use semantic requirements.

Restoring only prompt wording was insufficient: the earlier compatibility run still refused gross salary 0/3. After also restoring the original authoritative evidence presentation and removing irrelevant planning fields, an isolated replay recovered the full evidence and returned BDT 39,000 in 128.7 seconds (109.3 seconds preparation). A contemporary isolated archived-baseline replay answered in 104.4 seconds. These single observations do not establish a speedup or repeatable non-regression.

The subsequent isolated nine-case run still answered only one of three gross-salary cases (already-taxable and rebate cases both answered 3/3). The remaining difference in the authoritative source payload was the new work-group metadata. Those comparative fields are now omitted from authoritative planning/coverage inputs, preserving the original source payload shape; they remain available to factual/multi-perspective review and backend provenance.

With that final restoration, **all three gross-salary repeats answered BDT 39,000 with validated governing evidence**. Total times were 65.0, 76.2 and 106.8 seconds: p50 **76.2 seconds**, descriptive p95 **103.7 seconds**. Preparation times were 44.9, 59.6 and 88.0 seconds: median **59.6 seconds**, **41.6% below** the original 102.0-second baseline median. The proposed 40% preparation target is met in this three-observation comparison, not established as a production percentile guarantee. No refusal is included as an optimized success. Raw run: `exact-authoritative-payload.json`.

These turns made 20/24/24 embedding calls for 227/314/316 distinct missing texts and reused 185/174/177 exact embeddings. They reused 537/606/610 content loads. The baseline did not record equivalent aggregate provider counters, so no invented baseline call-count reduction is reported. Passage scoring now operates on the actual 40 rerank survivors instead of the 100 fused candidates when reranking succeeds. Hidden provider-internal retry counts remain unknown.

The first gross turn had no focused follow-up; the next two retained deeper recovery. First/subsequent total medians were 65.0/91.5 seconds, and preparation medians 44.9/73.8 seconds. The two subsequent observations have descriptive total p95 105.2 seconds. The first cohort has no meaningful p95. This explicitly avoids labeling these cohorts cold/warm caches.

Answer review remains mandatory. The later already-taxable run answered all three turns, but one incorrectly applied the 15% band's rate to BDT 500,000 despite its BDT 400,000 width, producing BDT 99,000 instead of BDT 104,000. That turn is a **quality failure**, not a pass. The final authoritative generation payload also omits the new comparative work-count instructions and work identity headers, restoring the original generation payload. Backend/UI work counts remain available. Final amount rechecks are recorded below. The summary script now reports expected-fact presence separately, so an answered turn with the wrong amount is visible; token matches still do not prove correct applicability.

| Final targeted case | Reviewed outcomes | Total p50 | Total p95 | Preparation p50 |
|---|---|---:|---:|---:|
| Gross salary, final generation payload | 3/3 BDT 39,000 with gross-to-taxable transformation | 87.2 s | 88.2 s | 70.4 s |
| Already-taxable income, restored generation payload | 3/3 BDT 104,000; no repeated salary exemption | 57.0 s | 58.8 s | 43.8 s |
| Current rebate | 3/3 lowest of 3% income, 10% investment, BDT 750,000 | 38.2 s | 43.7 s | 32.1 s |
| Conflicting fictional records, structured source lines | 3/3 correct source/date attribution and exact proof | 10.7 s | 14.2 s | 7.6 s |
| Competing accounts and reprint, final structured lines | 3/3 storm/strike attribution; two reviewed works | 14.8 s | 17.8 s | 9.5 s |

The final gross batch took 88.3, 87.2 and 59.8 seconds. Its preparation median is **31.0% below baseline**, so the earlier 41.6% result does **not** consistently establish the proposed 40% target. The final first/subsequent total medians are 88.3/73.5 seconds, and preparation medians 70.4/59.2 seconds; the two subsequent turns have descriptive total p95 85.9 seconds. Quality takes precedence over shortening deeper recovery. Raw final runs are `final-authoritative-gross.json`, `original-generation-taxable.json`, `structured-line-comparison.json` and `final-competing-accounts.json`; `final-comparison.json` consolidates the measurements and caveats.

The rebate cap can be expressed as 7.50 lakh; the fact-presence checker accepts equivalent Bengali digits and lakh notation. Gross and already-taxable baseline answers were also not fully `grounded` under the legacy claim verifier. The new safety checks intentionally leave unsupported arithmetic or uncited meta-statements unverified; a correct expected total is not a certificate for every sentence.

## Context ceiling experiment

The representative gross/rebate sweep tested ceilings 4, 8, 12, 16 and 24 through ordinary immutable configuration revisions. It ran before the final authoritative compatibility change. Results varied across questions: a small ceiling that answered a gross calculation could refuse a rebate question, and larger ceilings did not consistently resolve every gap. One observation per question/ceiling does not justify tuning to the fastest successful outlier. The original 24-chunk ceiling is retained. Complete admitted evidence units, amendment exclusions and rule applicability were not relaxed to fit a budget.

## Fictional facts and competing accounts

An isolated local evaluation Project, `d12397a8-c5a6-42d8-b5f1-5021deb9dd10`, contains five fictional documents and ten chunks. The fixture includes two conflicting film records, two competing accounts, and a reprint sharing the original account's `work_key`. The original factual/multi-perspective comparison ran 36 turns (six cases, three repeats, two approaches).

- Direct questions returned the correct director in all six runs. Median total time was 3.6 seconds for factual and 4.0 seconds for multi-perspective behavior, below the 15-second short-fact target. These are warm/first-process mixtures under concurrent evaluation load, not a controlled cache benchmark.
- Runtime comparisons returned the recorded 96 and 112 minutes. The claim verifier now leaves newly derived differences unverified when no supported calculation establishes them; surrounding semantic similarity cannot certify an invented number.
- Conflicting premiere records were correctly attributed as 1998 and 1999 in five of six original runs. One run selected a blank source line and failed closed. A later diagnostic reproduced the selector error, and a correction prompt alone did not fix it. The final v21 presentation uses structured line records carrying original numeric selectors beside their text. All three final comparison runs correctly attributed both dates with validated original source ranges and no recovery search: 10.7, 8.5 and 14.6 seconds (median 10.7 seconds). Raw run: `structured-line-comparison.json`. The bounded correction remains available; invalid proof still fails closed.
- Both explanations—Vale's storm and Marsh's strike—were preserved across all six original competing-account answers and all three final structured-line repeats. The reprint was not counted as a third work. Backend metadata recorded two reviewed works despite three cited documents. The final replies qualified their scope and did not choose a winner without evidence. This does not assert independent authorship or corpus-wide consensus.
- All six absent-revenue answers explicitly said the figure was not stated; none supplied fabricated revenue. The existing journey `no_answer` gate nevertheless failed because it expects pre-generation evidence refusal, whereas these answers disclosed the absence after ordinary factual admission.
- All six single-source answers stayed within the Marsh document scope. The multi-perspective answers identified the single available account and the lack of independent corroboration.

The raw journey result remains **failed**: the existing all-claims grounding gate also flags uncited comparative summaries and work counts. Manual answer review and legacy machine gates are reported separately; no gate was weakened to turn these results green. Raw run: `artifacts/rag-journey/evidence_approaches_v1/20260908T154619Z-6d4027d5/results.json`.

## Browser and automated checks

The authenticated local Operator Console displayed the new saved/current configuration table, translation disabled, evidence/claim-verification separation, and lifecycle/token details. A real streamed direct-film question showed searching progress before the answer, then the correct cited director in 5,854 ms round trip. It displayed one cited document, two reviewed works, one verified claim and coverage **not assessed**, avoiding an unsupported claim that ordinary relevance admission proved completeness.

Frontend validation: **87 tests passed**; TypeScript, ESLint and the production build passed. The complete final backend unit/architecture suite passed **1,096 tests** (20 existing deprecation warnings). The summary check was then extended with two equivalent-lakh cases; its five focused tests passed. Mypy passed all **441 application source files** and Ruff passed the changed application/tests/evaluation scripts. Provider failure, cancellation, separate-session recovery, snapshot changes, embedding identity, immutable configuration and unsupported arithmetic have dedicated regression coverage. ESLint excludes the existing local `node_modules.pre-validation` dependency directory.

## Deployment limits

Migration `20260908_0033` is additive and applied locally. Existing Projects without an evidence approach retain authoritative behavior; new Projects receive a normal factual revision. Source `work_key` changes use immutable metadata revisions. No blanket corpus reprocessing is required. Live deployment is separate. The legacy journey failures, unresolved historical year-only case, incomplete claim verification and inconsistent 40% tax latency target must remain visible when deciding rollout scope.
