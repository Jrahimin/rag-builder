# Income-Tax: reprocessing and recovery v14

Project: `f932c9e0-40af-4ff3-8704-ac3e542f2a8c`. Tested September 8, 2026, Asia/Dhaka.

## Live work completed

All ten documents were reprocessed serially and reached READY, with successful embedding jobs and index activation. The final active build is `2f358f7c`, replacing `98d07417`: ten documents and matching **2,039 chunks, vectors and keyword records**. The new job snapshot confirms chunker **3.3.0** and OCR ceiling **500**.

Source generation **35** and Project AI revision **6** were retained. No metadata or configuration edits were needed for this reprocessing pass. The configuration has 24 context chunks / 48,000 characters, 80 semantic and 80 keyword candidates, 40 reranked candidates and top K 12. Multiple Modifies targets remain preserved. The unresolved commencement of Ordinance 59 and the exact SRO 273 relationship must not be inferred from filenames.

The referenced task, **Review bounded turn-resolution PR** (`01a072b4-4ce8-7703-80fa-438bbd26a6da`), was read. Its resolver, parameter-safety and journey regression checks passed. This does not establish live acceptance of every follow-up scenario.

## Failures reproduced after reprocessing

| Case | Live result | Diagnosis |
|---|---|---|
| Original gross-salary question, conversation prefix `1c76dac9` | Refusal after 110,627 ms | Recovery v13 could not find the governing employment exclusion. Its focused retry almost repeated the unsuccessful query. |
| Explicitly already-taxable BDT 1,200,000, conversation prefix `3d1a72b6` | Incorrect BDT 99,000 after 109,526 ms | Generation applied 15% to BDT 500,000 despite the governing band's BDT 400,000 width. Coverage proved the right table, but all 21 discovery passages still reached generation. |

The already-taxable answer used the current 10% investment rebate, so this failure was not simply an old-year default. Only three passages were cited by the completeness proof. Old/future tables and worked examples in the broader discovery context introduced avoidable noise. The claim verifier also accepted the first valid equation in prose without checking a later equation or its explicit band width. The UI correctly displayed incomplete grounding overall (11/18 claims supported).

## Local fixes

- **Recovery v14** uses a distinct, bounded follow-up planner. It receives short excerpts from missing search branches, searches a compact source-language rule concept and may use an observed phrase as a second route. Examples supply vocabulary, never proof. Existing snapshot, scope, authority and quotation checks remain enforced.
- Generation receives only the passages cited by the validated completeness proof. The proof is checked again after pruning; required scope and conditions must also be quoted. `proof_chunk_ids` makes the selection inspectable without recording document excerpts in diagnostics.
- **Grounded prompt v12** explicitly allocates progressive bands by their stated widths, checks allocation and subtotal sums, distinguishes assessment years from income years, and covers applicable floors, caps and conditional exceptions. Calculated table rows require their own rule citations.
- A short canonical final check is placed after the untrusted evidence and conversation interpretation. It reinforces exception handling, concise calculations and correct table columns without adding another production provider call.
- Claim verification checks every explicit percentage equation and rejects allocation beyond an explicit English/Bangla `next` band in a pipe-separated source table. This is a narrow contradiction check, not a general legal-rule parser. Unknown syntax and unresolved applicability remain subject to existing checks.
- Markdown table headers are structural; data rows still require verification. Repeated paragraph headings preserve the original source envelope for citation offsets, as repeated table headings already do.

No tax rate, exemption amount or expected answer total was hardcoded into production behavior. No schema migration, metadata change or further blanket reprocessing is required by these runtime fixes.

## Validation

**444 conversation and journey unit tests passed**, including focused discovery, proof-only selection, band overruns in English/Bangla, multiple equations, table structure and heading provenance. Ruff and whitespace checks pass.

The user authorized model replays against the configured LLM provider using the selected uploaded excerpts. The focused planner generated `"সরকারি বেতন আদেশভুক্ত নয়" চাকরি হইতে আয়` without an expected rate or cap in its input. Running that exact query in live Search returned Nirdeshika PDF page 28, chunk 41, **first**, score **0.9240**, containing the governing employment exclusion.

Generation replays use the actual local prompt builder with the original gross-salary question and an explicit already-taxable variant. They use selected previously captured corpus passages, not a new deployed retrieval/coverage pipeline. Both produce the corpus-based totals below:

| Scenario | Taxable base | Slab subtotal | Investment rebate | Liability before payment credits |
|---|---:|---:|---:|---:|
| Gross salary; conditional resident ordinary private-sector employee, no other income | 800,000 | 45,000 | 6,000 | 39,000 |
| Income already taxable after exemptions | 1,200,000 | 110,000 | 6,000 | 104,000 |

The gross scenario subtracts the evidenced lower of one-third of employment income or BDT 500,000. The already-taxable case allocates 400,000 / 300,000 / 400,000 / 100,000 at 0% / 10% / 15% / 20%. These are validations against the project's uploaded corpus, not independent authentication of the underlying legislation.

Four generation replay pairs retained those totals. The final pair has correctly aligned calculation tables and assessment-year labels. The already-taxable answer explicitly mentions the BDT 1,000 new-taxpayer minimum; the gross answer still states only the general BDT 5,000 minimum. Both floors are below both calculated liabilities, so this omission does not change these totals, but exception reporting is not yet reliable enough to claim complete acceptance. Individual claim grounding was not validated through the live semantic provider for the new local code.

Replay artifacts and full job IDs are retained locally under `artifacts/tax-investigation/`: `live-v13-progress.md`, `focused-replay-v14.json`, and `generation-replay-v14*.json`. The artifacts are ignored by Git.

## Deployment and remaining acceptance

Deploy these runtime changes and use **new conversations**. Keep the existing active index and OCR setting; all ten documents have already been refreshed with chunker 3.3.0.

Repeat the original and already-taxable questions. Require recovery version v14, the correct assessment year and input treatment, governing citations, complete band allocation, rebate and applicable minimum-tax branches. Inspect `proof_chunk_ids` and claim diagnostics. Correct totals alone do not establish complete citation grounding.

Then test explicit historical periods, adjacent future-year tables, new-taxpayer minimums, changed scenario amounts and stale conversation bindings. The full `rag_journey.py` integration run requires its backend/database runtime; the local CLI unit tests and isolated provider replays do not substitute for that live acceptance run. The new runtime code has not been deployed during this investigation.
