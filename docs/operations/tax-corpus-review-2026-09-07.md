# Local tax corpus review — 7 September 2026

Project: `2ee2756f-ad27-44df-a9d3-1316b10ccbb1` (Income Tax & Budget).

## Findings and changes

- The local Console at port 5173 proxies to the API at port 8088. The active index uses Cohere `embed-v4.0`, 1024 dimensions, embedding set 3. The configured backend credentials support this provider. The reported `embedding_provider_unavailable` error was not reproduced in the subsequent local Test Lab requests. Preserve credentials required by retained rollback builds; changing the current default does not convert an existing index.
- The Finance Act amendment relationship had no effective date. Retrieval correctly treated that authority as unresolved, but recovery then lacked the period and applicable rules. Source revisions now record verified publication/effective dates and distinguish primary law, official guidance, and budget proposals.
- Metadata-only corrections to a base document could disconnect incoming amendment edges. Retrieval now follows the same document, source group and content hash across metadata revisions. Different content, groups and projects remain separate. Obsolete incomplete modifier revisions no longer poison a complete active revision.
- The source editor saved relationships, but its next-edit control showed “keep,” making persistence unclear. The editor now separately displays the saved Modifies/Replaces relationships. Saving another metadata correction preserves those relationships.
- Saving metadata during a document parse held an exclusive project-row lock while waiting for the document, blocking conversation/chunk foreign-key checks and causing a database deadlock. The metadata lock now uses PostgreSQL `FOR NO KEY UPDATE`: competing generation writes remain serialized, while unrelated project references can proceed. A real database concurrency test covers both properties.
- The canonical answer prompt used to demand a year whenever absent; repair planning did not receive the project's domain policy or trusted date. Both now receive trusted period context. Explicit user years take precedence. This project's default is the Bangladesh assessment year containing the reference date, so September 2026 selects 2026–27.
- English questions searched a largely Bangla corpus without project translation enabled. Translation is now enabled. The project uses a custom execution configuration derived from quality, with enough passage and context space for independent calculation rules. Search vocabulary distinguishes employment income excluded from total income from the general tax-free threshold. No rates or formulas were injected into the project instructions.
- OCR often produces one paragraph per line. Table chunking retained only the last line/footer, dropping the preceding year and taxpayer scope. Chunker 3.2.0 retains a bounded preceding passage across page breaks and repeats it with split table rows.
- The Bangla base Act failed because it has 316 pages while the local OCR ceiling was 300. The local ceiling is now 400. Profile validation still checks OCR provider/behavior compatibility but permits this operational resource ceiling to differ. Jobs retain the actual ceiling in their snapshots.
- A concurrent corpus change rejected an isolated index build with `index_build_corpus_changed`. The generic failure handler then marked the successfully parsed Nirdeshika as failed, causing later builds to omit its salary-exemption evidence. Corpus changes are now retryable build failures, and exhausting their retry budget does not invalidate the document. The complete active manifest must be checked after rebuilding; a successful processing job alone is insufficient.
- Extraction warnings double-counted pages that were both empty and unrecoverable. They now report total minus accepted pages. In the reviewed guide, PDF pages 2, 4 and 8 are blank; page 136 contains e-return screenshots rejected by the quality gate. The salary exemption and tax calculation pages were extracted.
- Recovery stays bounded and source-grounded. It searches up to four independent dependencies, has a 120-second deadline, admits up to eight chunks per dependency within the final context budget, and distinguishes timeout, provider failure and invalid model output in diagnostics. Planning and coverage receive trusted period policy and source identity; coverage also receives source roles/types/effective dates and page/chunk order. Conditional scenario inputs and already-qualified user inputs are distinguished from rules the corpus must prove. It must not invent missing rates or use an old year because that evidence is easier to retrieve.
- Repair planning previously copied obsolete/proposed numbers from unresolved excerpts or searched for the user's amount, overmatching worked examples. Planning now uses source identity hints rather than unresolved excerpt text, and omits scenario amounts from rule queries. A passage found in one branch may support another dependency if it survives the final admitted context; the branch is a discovery route, not an authority boundary.
- Short passage labels are mapped back to real chunk IDs after coverage review, reducing confusion between excerpts from the same file. Quote validation tolerates OCR line wrapping and spacing around punctuation while retaining every word, numeral and punctuation token. Changed numbers, added negation, missing punctuation, unknown IDs and omitted context still fail. The captured previously rejected quotations all validate under this formatting-only rule.
- A high similarity score could bypass completeness review entirely: the already-taxable-income test copied a special-category worked example's BDT 450,000 threshold and produced BDT 91,500. Governed calculation requests now enter completeness review even when initial evidence is relevant. A failed review cannot fall back to the original examples. The corrected case returns BDT 104,000.
- Test Lab could send into the previous conversation while a new conversation request was pending. Creation and sending now exclude each other, including form/Enter submission. A regression test reproduces the race. Numbered Markdown headings no longer split at their ordinal into spurious factual claims. The generation prompt requires citations on individual calculation steps/table rows.

## Source configuration

Publication date describes the particular file/edition. Effective dates describe its legal or guidance applicability. An amending Act is a separate document linked with **Modifies**; it does not replace the entire base Act. A translation is a separate representation, not a legal amendment.

| File | Role and relationship | Dates / scope |
| --- | --- | --- |
| Income Tax Act 2023 Bangla | Primary base Act, independent source group | Published/effective 22 June 2023; open-ended, subject to amendments |
| Income Tax Act, 2023 English | Primary authentic English text, separate source group | This edition published 16 October 2025; underlying Act effective 22 June 2023. Do not confuse edition publication with enactment |
| Finance Act 2026 Bangla | Primary amending Act; Modifies both stored representations of the 2023 Act | Published 30 June 2026; relevant income-tax amendments effective 1 July 2026. Some other provisions have separate commencement rules in the text |
| Income Tax Nirdeshika 2026–27 | Supporting official guidance; independent annual source group | Published 1 September 2026; guidance period 1 July 2026–30 June 2027 |
| Income Tax Paripatra 2026–27 | Supporting official circular; independent annual source group | Published 2 September 2026; guidance period 1 July 2026–30 June 2027 |
| Budget Speech 2026–27 (already uploaded) | Reference background/proposals; independent | Cannot establish enacted rates where current law/guidance differs |
| Sample test tax paper (already uploaded) | Draft test fixture/reference | Excluded from retrieval. Retired alone is insufficient: an unreplaced retired source can remain eligible by design |
| Certain Laws Relating to Finance Ordinance 2025 (52) | If added: primary amending ordinance, Modifies the affected Act(s) | Read commencement and affected provisions; do not replace the complete 2023 Act |
| Income Tax (Amendment) Ordinance 2025 (59) | If added: primary amending ordinance, Modifies the 2023 Act | Preserve its own publication/effective dates and the later 2026 amendments |
| Withholding Tax Rules 2026, SRO 210 | If added: primary delegated rules, own rule-set source group | Subordinate rules under the Act; not a replacement of the Act |
| Withholding Tax Rules amendment, SRO 273 | If added: primary amendment, Modifies SRO 210 | Apply the amendment's commencement and actual rule scope |
| I.T. Manual Parts I and II (2015) | If added: historical reference, separate groups | Do not promote 1984-era provisions to current 2026 authority |
| FAQ e-return 2025 | If added: supporting procedural FAQ/reference | Filing procedures; cannot override enacted tax computation rules |

Only the project's existing seven nondeleted documents were reconfigured. The other files in the Downloads folder were reviewed as possible additions, not silently uploaded. Finance Act 2026 changes many provisions; the relationship deliberately does not invent an exhaustive provision list. Current operative passages must establish the relevant rule. Partial amendment text cannot alone establish the entire consolidated law.

## Calculation acceptance case

Original question: yearly salary BDT 1,200,000, rebateable investment BDT 60,000, male, below 40, Chittagong; no year specified.

For a **conditional resident ordinary private-sector employee estimate**, the supplied current guide supports:

1. Assessment year 2026–27, stated explicitly as the default.
2. Gross employment income 1,200,000; exemption is the lower of one-third and 500,000, giving 400,000; taxable income 800,000.
3. General slabs: first 400,000 at 0%, next 300,000 at 10%, remaining 100,000 at 15%: gross tax 45,000.
4. Investment rebate uses the smallest applicable amount: 3% of eligible income, 10% of eligible investment, and 750,000. Here the investment limb is 6,000; the resulting amount is 39,000 before any applicable additional adjustments or tax already paid.
5. State the applicable minimum-tax check and distinguish conditional relief, surcharge, source deductions and payment credits. Do not silently apply early-filing relief or assume the user's residency/employment category is known.

Finance Act 2026 PDF page 76, clause 61, expressly changes section 78's `0.15` to `0.10` and its ten-lakh cap to 7.50 lakh. This is stronger evidence than a worked example or a budget proposal.

## Verification

All seven nondeleted documents are READY with no document errors. The active build is `42b63e3c-b55b-4c37-86d8-66947c1fd497`, containing seven document versions and 2,112 chunks. Source generation is **16**; draft-source filtering excludes the sample independently of its physical presence in the index. Finance is processing version 3, Bangla Act version 3, Nirdeshika version 4 and Paripatra version 2. Source state was reread through the application service; Finance retains both Modifies links after a metadata-only browser save.

The latest saved Project AI revision is `806c60fe-53ce-4012-b36d-b52de8b7705b`. Its execution configuration uses quality-derived values: 80 semantic/80 keyword candidates, 40 rerank candidates, retrieval top 12, six chunks per document, three per section, 512-token scored passages with 64-token overlap, and a final limit of 24 chunks/48,000 characters. Relevance, source-authority and claim-verification thresholds were not lowered. The behavior policy enables translation, current-assessment-year defaults, conditional resident/private-sector estimates, distinct rule searches and individually cited prose calculation steps. Existing conversations retain their immutable configuration snapshots; create a new Test Lab conversation after changing Project policy.

| Case | Result | Persisted conversation |
| --- | --- | --- |
| Exact original question, browser Test Lab | AY 2026–27; taxable salary 800,000; gross tax 45,000; 10% investment rebate 6,000; conditional estimate **39,000** | `157c695a-819b-4555-b275-2d9571d2b12c` (browser tab retains this conversation) |
| Same original question through the same application services | **39,000**, rules recovered and coverage quotes validated | `f5fa6f29-9dbd-4af1-8ee3-eb06cd49a034` |
| Explicit AY 2026–27, income already taxable at 1,200,000 | **104,000**; salary exemption not deducted again; `calculation_completeness` review recovered the governing rules | `9b40859d-480a-47a3-9a6c-b715c3e52467` |
| Explicit AY 2024–25 | Refused because the supplied evidence did not establish that historical calculation; did not substitute current-year rules | `dd15c6f2-73b0-4aab-85a1-1edc12041afd` |

The final combined backend regression run passed **294 tests**. Three PostgreSQL integration tests (including real row-lock concurrency), 18 Project Administration tests and 13 Test Lab tests also passed. Frontend TypeScript checking, Ruff and `git diff --check` pass. The backend run emitted five existing SWIG deprecation warnings. Scratch investigation artifacts and provider transcripts remain ignored under `artifacts/tax-investigation/`.

## Remaining limits and local-to-live handoff

- The **numerical acceptance cases pass**, but Test Lab still reports **grounding incomplete**. In the browser case it supported 12 of 27 generated claims; remaining items include scenario assumptions, formatted calculation fragments and uncited qualifications. The backend did not mark these answers fully grounded, and this review does not override that result. Do not treat a correct total or the presence of citations as proof that every generated statement is independently verified.
- The original Taskiq worker is owned by a Windows process that denied termination. Its outstanding jobs finished, and a restart was requested from the user so future jobs load the fixes. Restart it from its existing terminal using `python -X utf8 -m taskiq worker app.worker.entrypoint:broker` from `backend`. The API is running and browser requests show the updated prompt/repair versions. The temporary investigation API process was removed, leaving the original local API.
- These code and audited source/AI-configuration changes are **local**, not deployed to live. Before live use, apply the reviewed source metadata and project policy in the live project, deploy the code, restart workers, ensure the active/rollback embedding providers retain credentials, raise the OCR cap for the 316-page Bangla Act, rebuild with recovered text/context, and verify the complete active manifest. Local database UUIDs should not be copied blindly into a different live project.
- The Downloads files not already in the project remain unuploaded. Missing historical acts/ordinances and other source gaps can still warrant a refusal. The Finance amendment relationship intentionally remains unscoped rather than pretending to contain an exhaustive list of its affected provisions.
