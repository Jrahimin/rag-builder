# Income-Tax rollout and acceptance checks

Target project: `f932c9e0-40af-4ff3-8704-ac3e542f2a8c`. These are operator steps after deploying the code; deployment does not mutate existing project policy, metadata or immutable chunks. Do not apply these tax-specific settings globally.

## Code behavior

- Unresolved indexed authority/completeness now prevents both indexed-then-web fallback and indexed-and-web supplementation from generating an answer. Web snippet relevance is not a legal authority check. Other insufficient-relevance requests retain web fallback. A future validated web recovery path must establish period, commencement, conflicts and every required calculation dependency before generation.
- Native date input events update source drafts immediately on both source-editing surfaces. Save responses are checked against submitted dates. This addresses an input-event failure mode and prevents a mismatched response being reported as success; verify the actual deployed save/reopen journey too.
- Every Modifies target has an optional editable provision list. Use one exact provision heading per line; blank lines and duplicates are removed on submission. Blank scope means an unresolved document-level relationship, not whole-document repeal.
- An explicitly scoped amendment no longer blocks unrelated, fully headed provisions. Unknown headings, unheaded continuations, overlapping subsections and unscoped relations remain conservative. The system still cannot infer commencement from publication.
- Recovery diagnostics v12 include context limits and original/translated branch execution counts. Original Bangla searches are retained; translation was not replacing them in the existing implementation.

## Source corrections

Verify against the supplied PDF text and the authentic instrument before treating these as authoritative legal metadata. Local copies were reviewed; live-file hashes were not authenticated.

| Source | Configure | Dates / verification |
| --- | --- | --- |
| Bangla Act 2023 | Primary Act, independent base | Published/effective 2023-06-22 |
| English Act 2023 | Primary Act, separate language edition | Edition publication 2025-10-16; underlying effective 2023-06-22 |
| Finance Act 2026 | Primary amending Act; Modifies both base editions | Publication 2026-06-30; relevant tax provisions effective 2026-07-01. Preserve any provision-specific commencement exceptions; do not enumerate only section 78 as if exhaustive |
| Ordinance 52/2025 | Primary amending ordinance; Modifies both base editions | Supplied gazette publication/effect 2025-10-06, immediate commencement. Income-tax amendments concern sections 106 and 166; enter exact headings only after matching each edition |
| Ordinance 59/2025 | Primary amending ordinance; Modifies both base editions | Publication 2025-11-06. Commencement is conditional on a different instrument; obtain that evidence before assigning effective date |
| SRO 210/2026 | Primary delegated rules, independent rule-set | Publication 2026-06-08; effective 2026-07-01 |
| SRO 273/2026 | Primary delegated instrument; currently Modifies SRO 210 | Publication 2026-07-05; explicit effective 2026-07-01. Full 68-page text requires checking whether reissue/replacement or amendment before changing relationship |
| Nirdeshika 2026–27 | Supporting official_guidance; independent annual edition | Publication 2026-09-01; guidance period 2026-07-01 through 2027-06-30 |
| Paripatra 2026–27 | Supporting official_circular; independent annual edition | Publication 2026-09-02; guidance period 2026-07-01 through 2027-06-30 |
| e-return FAQ | Reference procedural FAQ; independent | Leave unsupported dates empty; cannot override enacted tax rules |

For each correction, save, reopen and compare every date and Modifies target. Publication and commencement are different fields. Metadata changes create revisions and do not require OCR by themselves. Multi-target links already saved in the live investigation must be preserved.

## Project AI configuration

Keep evidence thresholds unchanged. Use the previously successful local configuration as a starting point for measured live evaluation:

| Setting | Value |
| --- | --- |
| Semantic / keyword candidates | 80 / 80 |
| Rerank window / top K | 40 / 12 |
| Maximum chunks per document / section | 6 / 3 |
| Passage window / overlap tokens | 512 / 64 |
| Context chunks / characters | 24 / 48000 |
| Query translation | Enabled |

Append this policy to this project's existing domain instructions. It contains no fixed tax rates:

> When no assessment year is specified, use the Bangladesh assessment year containing the trusted reference date: July through June, named by its start and end years. State the assumed assessment year. An explicit user year takes precedence, including historical years. Do not select an old year merely because that text is easier to retrieve.
>
> Treat an unqualified annual salary as gross employment income, and separately establish any amount excluded from total income before applying the general tax-free threshold. If the user explicitly supplies taxable income, do not deduct the salary exclusion again. For an estimate with unspecified residency or employment category, clearly state a conditional resident ordinary private-sector employee scenario and identify where another category would change the calculation. Do not treat assumed category membership as a rule the corpus must prove.
>
> Retrieve and cite each independent calculation rule: employment-income exclusion (including চাকরি হইতে আয় / মোট আয়ের বহির্ভূত আয়), applicable taxpayer threshold and complete slabs, investment rebate and limits, and minimum tax. Apply the current amendments with their own scope and commencement. Distinguish tax liability from source deductions/payment credits, surcharge and conditional relief. An estimate may be before payment credits, clearly labelled. Missing rule evidence must produce a specific evidence-gap response, not a guessed total or zero exemption. Budget proposals cannot establish enacted rates.

Create new test conversations after changing policy; existing conversations retain configuration snapshots.

## Reprocess and activate

The reviewed active build `19dd2ec8` contains ten documents and 1,929 chunks, built at 01:29 Dhaka on 7 September. Its processing history records chunker 3.1.0. Restart API and workers on the deployed code and verify new job snapshots say **3.2.0**. Ensure the operational OCR ceiling accommodates the 316-page Bangla Act (for example 400); do not lower extraction-quality checks.

Reprocess documents whose stored chunks predate 3.2.0, particularly Finance, both base Acts, Nirdeshika and Paripatra. Process serially when automatic builds are enabled, waiting for each complete job/build, to avoid competing active-corpus snapshots. Rebuilding vectors from existing chunks alone will not apply the new table context. Verify the final manifest includes all ten READY document versions and matching chunk/vector/keyword counts. Keep the previous build and its embedding credentials for rollback.

## Acceptance

Run the exact original salary question in a new conversation, then explicit-gross, already-taxable, historical-year and Bangla variants. The supplied-current-corpus conditional gross-salary benchmark is 39,000 BDT (800,000 taxable, 45,000 before rebate, 6,000 rebate); already-taxable 1,200,000 is a separate 104,000 benchmark. These values are tests against the supplied corpus, not independently authenticated filing advice.

Require correct per-rule citations, applicable year/category, complete evidence and correct arithmetic; finding an expected number alone is insufficient. A failed authority check must yield a backend-classified insufficient-evidence answer with no web-generated speculative subtotal. Confirm recovery v12 and the new context limits in diagnostics. Exercise save/reopen with both language targets and date input before blur; verify exact dates in returned revision and reopened form.

## Local verification

The conversation/source regression suite passed 370 tests; the final scoped-authority check passed 20 tests. Frontend source metadata, relationships and Test Lab suites passed 27 tests, including native date input and multi-target save/reopen. The production frontend build passed. These checks do not establish that the live corpus or project policy has been updated; repeat the live acceptance journey after the steps above.
