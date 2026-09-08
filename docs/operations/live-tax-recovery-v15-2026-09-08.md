# Income-Tax recovery v15: full-pipeline investigation

Tested September 8, 2026, Asia/Dhaka. Local Project `2ee2756f-ad27-44df-a9d3-1316b10ccbb1`; live Project `f932c9e0-40af-4ff3-8704-ac3e542f2a8c`.

## What caused the recurring failures

The earlier selected-excerpt replays proved generation could calculate from the right passages. They did not prove the deployed retrieval and admission pipeline would find and retain those passages. Full local replays exposed several independent failure paths:

1. **The U.S. sources were real web fallback results.** The follow-up resolver dropped Bangladesh from its effective question. Indexed candidates failed admission; recovery did not run for that failure class. A generic web query then returned U.S. tax pages, and a shared-word relevance check admitted them. This was not evidence of cross-Project vector contamination or the model inventing the source nationality.
2. **Similarity was being confused with applicability.** A high-scoring company-tax or proposal passage could bypass recovery for a short “current” rule question, especially after the conversation resolver timed out. Conversely, a strong reranker match could be rejected for weak independent semantic/lexical corroboration without attempting a better rule search.
3. **Whole-question and single-language searches missed different rule components.** In provider-backed comparisons, English found the current rate table while compact Bangla found the employment-income exclusion. Combining both languages into one long query did not reliably recover both. Worked examples, forms and special-category thresholds competed with governing provisions.
4. **Recovery could discard its own progress.** An empty discovery route stopped the process; repeated earlier hits crowded out newly found rules; a generated search's incidental year became a new coverage requirement. Reading adjacent chunk indices also missed page continuations because table evidence units are not necessarily indexed in physical page order.
5. **The completeness protocol itself caused false refusals.** Requoted OCR punctuation failed exact proof checks. Another captured response said `complete: true` while marking several checks unsupported. Optional earning dates or asset facts also became blocking requirements despite a permitted conditional salary estimate.

The supplied failed responses and local runs show matching Cohere `embed-v4.0` embedding identities and applied `rerank-v4.0-pro`. No observed identity mismatch or corrupt index explains these failures. Rebuilding the same corpus again would not repair these runtime decisions.

## Code changes

- Recovery v15 plans up to four necessary concepts, with separate user/source-language routes when useful: eight initial searches, at most two additional rounds of two searches, within the existing 120-second recovery deadline. Searches remain sequential because the adapter shares a database session. Optional query translation remains independent and disabled locally.
- Empty routes may use proof found by other routes. Subsequent rounds retain exactly cited confirmed passages, while diverse discovery excerpts provide vocabulary for missing rules. Search wording is not passed to the completeness reviewer as a requirement.
- Completeness proofs select numbered source lines; the application reconstructs the exact original text and still validates every quote against the final generation context. Contradictory complete verdicts receive the existing single format retry with validation errors, then fail closed if still invalid.
- Adjacent-page recovery is used only when the reviewer identifies an actual governing continuation and cites it. Neighbours must be in the same Project, document version and active index build. They pass ordinary source policy, filtering, reranking and admission; they inherit no relevance score. Page neighbours fall back to chunk neighbours only for unpaged text.
- Current-rule questions with governed/reference sources receive applicability review even when similarity admission passed. Strong reranked candidates rejected for relevance can trigger recovery without lowering admission thresholds.
- Turn resolution preserves jurisdiction. Web fallback includes trusted Project scope and the reference date. A bounded, source-quoted review rejects foreign or otherwise inapplicable web evidence before generation; unavailable or invalid review fails closed.
- Grounded prompt v13 distinguishes assessment labels from earning dates, checks full bands and minimum-tax exceptions, and keeps follow-up rate questions focused on the rule. No tax rate, exemption amount or expected final total is hardcoded into production code.

This follows the useful separation between retrieval, contextual relevance and answer evaluation described in [Microsoft's advanced RAG guidance](https://learn.microsoft.com/en-us/azure/developer/ai/advanced-retrieval-augmented-generation) and [Anthropic's contextual retrieval work](https://www.anthropic.com/engineering/contextual-retrieval). These techniques improve evidence discovery; they do not authenticate legislation or make an LLM completeness judgment infallible.

## Local corpus and saved settings

Five missing documents were uploaded through the normal audited document service and processed by the running worker. All twelve local documents reached READY. Active build: `bc61c185-faef-41e3-93eb-74a80f534c07`, twelve documents and 2,403 indexed chunks. The sample/test document is draft and excluded by source policy.

| Document family | Configuration and relationship |
|---|---|
| Income Tax Act 2023 Bangla and English | Separate primary source editions; both remain targets of relevant amendments. |
| Finance Act 2026 | Primary amendment; Modifies both base editions. It does not replace the entire 2023 Act. |
| Ordinances 52 and 59 of 2025 | Primary amendments targeting both base editions. Ordinance 59's unverified commencement remains unknown; a date was not invented to remove a warning. |
| Nirdeshika and Paripatra 2026–27 | Current supporting official guidance; operative text must establish each rule's scope and period. |
| SRO 210 and SRO 273 | SRO 273 is a later revision replacing SRO 210, rather than merely an unrelated amendment. Its indexed rule 14 expressly repeals SRO 210 while saving earlier actions/proceedings. |
| Budget Speech and FAQ | Reference sources; proposals cannot establish enacted rates. |

Local AI configuration revision `a6e44b70-0df6-4031-99a7-20e33e3a42e8` saves translation disabled, `indexed_then_web`, explicit Bangladesh/assessment-year defaults and a conditional ordinary salary-only estimate. It distinguishes missing asset facts/payment credits from missing governing rules. It contains no numeric tax benchmark. Candidate/context settings remain 80 semantic, 80 keyword, 40 reranked, top 12, 24 context chunks / 48,000 characters.

The exact saved configuration and metadata inspection are retained locally in `artifacts/tax-investigation/local-config-v15-final.json` and `final-source-configuration.json`. Artifacts are local investigation files, not committed fixtures or runtime answer shortcuts.

On live, SRO 273 metadata was saved as the later revision of SRO 210, with the replacement title, primary role and existing verified dates. Reopening confirmed revision 2, title and dates; source generation advanced from 35 to 36. The Projects screen independently confirmed **Saved relationships: Replaces Withholding Tax Rules 2026 - SRO 210/2026**. The live active build remained `2f358f7c-1c1f-4a51-acc5-46bcb489e0a6`; no blanket live reprocessing was repeated during this investigation.

## Validation and practical limits

Full replays create persisted conversations in the actual local Project and call the production ChatService, retrieval adapter, database, configured embedding/reranking providers and LLM. They capture every search and provider boundary. They are not selected-excerpt generation tests. `rag_journey.py` unit checks are included; its separate seeded fixture run was not represented as a test of this real corpus.

The uploaded-corpus benchmark is AY 2026–27: salary 1,200,000; exclusion 400,000; taxable income 800,000; tax before rebate 45,000; rebate 6,000; final base income tax 39,000. The evidence also states ordinary/new-taxpayer minimums of 5,000/1,000, both nonbinding here. These are regression expectations from the supplied corpus, not independent authentication of current Bangladesh law or the screenshot's enactment date.

| Captured replay | Result |
|---|---|
| `separate-language-routes`, conversation `4f468630-3fb7-4a45-b7d0-01d1dd0d6917` | Both answers correct against the corpus benchmark; 92.8s salary, 61.8s rebate. |
| `separate-language-repeat`, conversation `519ec723-8d66-40f6-be15-367acf9d513b` | Both refused. Exposed unnecessary neighbouring-page recovery, expanding requirements and contradictory complete verdict. These failures were retained, not counted as passes. |
| `bounded-review-final`, conversation `49312f7e-bb02-47d2-8c35-d604bec970bf` | After those fixes: salary 39,000 with both minimums; rebate 10% with income/absolute limits; 99.3s and 66.7s. No web fallback. This conversation predates the final saved scope clarification. |
| `final-saved-config`, conversation `0ea52fed-049d-4e0b-8fe3-232bd03c7257` | Final saved configuration: salary 39,000, all transformation/band/rebate/minimum components and conditional scope stated; rebate 10% with both caps. 126.8s and 55.0s. Both recovered, generated from knowledge, and used no web fallback. |
| Foreign web evidence replay | New review rejected all eight actual U.S. excerpts from the supplied failed response. |

Final checks: **589 unit tests passed**, covering conversations, retrieval and `rag_journey.py`; Ruff and `git diff --check` passed; mypy passed on seven changed core files. Existing deprecation warnings remain. The last two full-pipeline runs passed both corpus answer benchmarks, but their per-claim grounding flags were false as described below.

The existing per-claim grounding badge remains conservative: correct scenario inputs, assumptions and multi-step derived table rows can still be marked unsupported/unverified because its verifier mostly compares answer text with source text and supports a narrow percentage-arithmetic grammar. Correct final amounts are not grounds to force that badge green. A future calculation provenance model should distinguish user inputs, quoted rule parameters and checked derived expressions, retaining source checks for the rule parameters. This remains a limitation of the current response verification, separate from pre-generation refusal.

Local Console remains at operator sign-in, so browser authentication and the UI send path were not validated. The persisted service/provider runs validate the actual local RAG flow but do not establish deployed HTTP/UI acceptance. Latency is still material, and model-driven planning/completeness can vary; do not infer production-wide reliability from these two questions.

## Deployment and retest

Deploy the code and restart the API/workers. These runtime changes require no database migration or blanket reprocessing. Retain the active embedding provider credentials. Apply the saved local Project policy and translation setting to Income-Tax live through the normal configuration UI; code deployment alone does not update Project configuration. Start a new test conversation because existing conversations retain configuration snapshots.

Retest the exact salary question and rebate follow-up, then the explicitly already-taxable variant, explicit historical period, and a low-income case where the minimum-tax alternatives can change the result. Inspect the actual selected proof passages, source generation/build, recovery status and web scope diagnostics. Do not merely compare the final number or accept a green similarity score as proof of applicability.
