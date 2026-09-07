# Live Test Lab investigation, 6 September 2026

## Verified result and scope

For AY 2026–27, assuming ordinary private-sector employment, eligible residency,
gross employment receipts of BDT 1,200,000, qualifying investment of BDT 60,000,
and no other income or surcharge, the base liability is BDT 39,000:

- Sixth Schedule, Part 1, paragraph 27: exempt the lesser of one-third of
  employment income and BDT 500,000. Exemption 400,000; taxable income 800,000.
- General individual slab: first 400,000 nil, next 300,000 at 10%, remaining
  100,000 at 15%. Liability before investment rebate 45,000.
- Amended section 78: minimum of 3% of applicable income, 10% of permitted
  investment and 750,000. Rebate 6,000; base liability 39,000.

This is before filing-time incentives/additions and withholding credits. The NBR
guide distinguishes employees covered by government salary orders, so the
employment assumption must be stated. The applicable minimum tax does not bind
at this amount. The original question did not explicitly supply an assessment year.

Primary references consulted:

- [NBR Income Tax Guide 2026–2027](https://nbr.gov.bd/uploads/publications/আয়কর_নির্দেশিকা_২০২৬-২০২৭.pdf):
  printed pages 10 (general rates), 20 (employment exemption), 53–60 (rebate).
  The live indexed guide independently returns the exemption on PDF page 28,
  chunk 29, and the general rate table on PDF page 18, chunk 17.
- [NBR Finance Act 2026](https://nbr.gov.bd/uploads/acts/Finance_Act_2026.pdf),
  listed on NBR's enacted Finance Acts page. Live indexed PDF page 142, chunk 178
  contains the 400,000 slab; chunk 179 contains the current special categories
  and the following period heading.
- The live indexed NBR Paripatra 2026–2027, PDF pages 73–74, chunks 74–75,
  explicitly describes the section 78 change from 15%/1,000,000 to
  10%/750,000, with the 3% income limit retained.

[BSS's passage report](https://www.bssnews.net/js-session/400981) corroborates
the 400,000 threshold. PwC's July 2026 deductions and sample-calculation pages
corroborate exemption/rebate changes, but the sample still displays a 375,000
threshold and inconsistent arithmetic; it was not used as the calculation oracle.

## Evidence and root causes

The supplied trace is from build `fbb1df48-8c75-4ebf-84f1-5272017175c6`, source
metadata generation 24. Its first turn bypassed history resolution correctly.
The pasted export is not valid JSON (unescaped quotation marks in excerpts);
the investigation read its literal fields and verified sources through live search.

1. **A future period was detached from its table.** Paripatra chunk 15 ends with
   the heading for AY 2028–29 and 2029–30. Chunk 16, ID
   `5dc28f3a-73fd-4810-a686-d43e0fe25d43`, contains only the 450,000 table and a
   generic caption. This particular winning table is a future-period table,
   not the women/65+ table. The circular's 2026–2027 title does not date every
   provision within it. Live search confirms both adjacent chunks.
2. **Necessary dependencies were not recalled.** The employment exemption and
   section 78 amendment are already indexed and retrievable with focused queries.
   The original compound question selected an older rebate and isolated slab,
   plus unrelated forms/investment passages. Strong relevance is not completeness.
3. **Incomplete authority metadata silently became neutral.** Five MODIFIES
   records had incomplete governance metadata, missing modifier effective dates
   and empty provision scopes. Expansion excluded them; redaction only handled
   recalled, exactly scoped modifiers. The base Act remained usable without a
   runtime authority limitation. Diagnostics even said scope was not applicable.
4. **Generation assumed away missing rules.** Prompt v8 requested arithmetic
   from supplied quantities without an explicit applicability/input-basis check.
   The model relabeled salary as taxable total income and assumed no exemptions.
5. **Grounding measured evidence similarity and arithmetic.** Matching text,
   multilingual embeddings and correctly applying a cited percentage did not
   establish the correct period, category, adjusted base or current legal effect.
   Quantity setup segments were not independently verified as source claims.

The synthetic tax Journey sources have explicit Section/ধারা headings, dated
MODIFIES edges and exact matching scopes. They test the governed happy path;
they did not reproduce the real incomplete metadata and detached table shape.

## Focused implementation

- Chunker 3.1 carries heading ancestry and the preceding source element into
  table content, including repeated row groups. Oversized/missing context is
  explicit. Tiny-chunk merging cannot cross table boundaries. A scored numeric
  passage cannot replace the full table's scope. Source envelope offsets and
  exact evidence hashes remain distinct for repeated context.
- Runtime authority annotation preserves source text, identifies unresolved
  amendment scope/metadata or missing table context, and rechecks selected
  evidence after budgeting. It does not invent effective dates, amendment targets
  or document-wide repeal. Inactive/out-of-period edges remain nonapplicable.
- Prompts include structural metadata and authority limitations. Canonical v9
  requires applicability and input transformations before calculation, and a
  partial answer when required rules are missing. No tax constants enter runtime.
- Claims retain `evidence_support` independently from `authority_status`.
  Known unresolved authority prevents a false supported/grounded result.
  `not_assessed` explicitly does not claim that legal applicability was proved.
  Citation snapshots and structured EN/BN notices retain the limitation.
- The profile registry and generated OpenAPI/TypeScript contracts are updated.

## Tests and live verification

The full deterministic run passed 940 tests, with two optional PaddleOCR tests
skipped. Backend mypy checked 457 files; Ruff lint/format and frontend TypeScript
checks passed. The final focused run passed 102 tests, covering table-span and
heading-ancestry changes, incomplete authority diagnostics, historical edges,
missing modifiers, and exact lexical/arithmetic matches with unresolved authority.

Fresh live conversation `c1e3c385-3f46-47de-9d23-40f1c30b45ae` reproduced the
91,000 answer on the original question on 6 September, including the gross-to-
taxable substitution, future table and older rebate. This is a baseline against
the deployed backend, not evidence that the local patch has been deployed.

A second live message specified AY 2026–2027, ordinary private employment and
gross salary, and requested the current NBR rules without supplying any expected
rates or result. The model withheld a final estimate because necessary evidence
was missing, but still quoted the older 15% rebate component conditionally.
Selected guide chunks were return forms, not the relevant rule pages. This
demonstrates that clarifying the question helps abstention but does not repair
compound-query recall or establish current authority.

Live search and both messages therefore validate the failure and retrieval gap,
not the patched deployment. No deployment route was available in this workspace;
the API/worker rollout, metadata curation and chunker 3.1 index rebuild remain
required before a post-fix live acceptance result can be reported.

## Rollout and remaining limits

Deploy API and worker from the same change. Reprocess and rebuild affected
documents using chunker 3.1, retaining the previous immutable build for rollback.
Do not fabricate complete amendment metadata or globally replace the Act to make
this one question pass. Curate effective dates/provision scopes from source text.
Then rerun the original question and explicit AY/private-employment variant in
Test Lab, inspect selected spans/authority notices, and check the base calculation.

This focused patch is not a general legal reasoner or a multi-step retrieval
planner. It makes known uncertainty explicit and improves evidence preservation;
prompt compliance is not a deterministic semantic applicability proof. A complete
answer may still require focused retrieval when the initial recall omits a rule.
Automatic dependency retrieval and generalized semantic applicability verification
are deferred rather than added as a large architecture change in this fix.

## Live rollout follow-up, 7 September 2026

The user confirmed deployment. The live document job snapshot independently
confirmed chunker 3.1.0 and profile registry 2026-09-06. Through Test Lab, the
English Act, Finance Act, Nirdeshika and Paripatra were reprocessed successfully.
Processing versions are respectively 3, 2, 2 and 2. Automatic embedding/index
jobs activated build `19dd2ec8` containing all 10 project documents and matching
counts of 1,929 chunks, vectors and keyword records. A redundant manual rebuild
was not requested. The original 10-document build `fbb1df48` remains retained.

Fresh conversation `344bf4cf-8bb4-45da-afe5-5be4eaa9239c` did not pass acceptance.
The original English question produced a conditional BDT 88,500 estimate using
the older rebate and unresolved 450,000 table, additionally applying the 15%
band beyond its stated width. The updated inspector showed 7 of 27 claims
supported and 20 needing review. Refreshed table chunk `cd16e207` (page 16,
chunk 31) still had unresolved applicability. Reprocessing and warning display
are therefore verified, but correct tax generation is not.

No legal metadata was invented or changed. The source correction UI exposes
document dates and treatment, but not amendment target provisions. Document-wide
dates and a MODIFIES label cannot substitute for provision-level legal effect.
Metadata generation remains 24; source-backed amendment curation remains open.

## Runtime recovery fix after the refreshed-corpus failure

The remaining executable defect was the gap between relevance admission and
authority usability. `assess_and_select_knowledge` returned sufficient evidence
despite selected chunks being marked `authority_status=unresolved`.
`INDEXED_THEN_WEB` consequently never invoked recovery. Generation could ignore
the warning, and post-generation claim checks only exposed the problem after
the answer had already been produced. This explains continued use of the older
rebate even after successful reprocessing.

Known unresolved authority now makes the selected evidence insufficient before
generation, in enforce and observe modes. One bounded recovery pass can plan up
to three focused searches for distinct dependencies. Every search retains the
original document, metadata and historical filters; differing index builds or
source generations abort recovery. Initial incomplete amendment relationships
remain in force across searches, preventing an older rule from regaining trust
when a branch omits that relationship. Each dependency must retain admitted,
authority-usable evidence within the final context budget. Query planning never
supplies answer evidence. The pass has a 30-second limit, accounts for LLM usage,
and records `knowledge_repair` diagnostics while honoring candidate-trace settings.

Failed recovery uses the existing allowed web fallback or a structured
`unresolved_authority` response; unsafe indexed rules do not reach generation.
This is a narrow addition to the existing retrieval pipeline, with no tax
constants in runtime code, no new environment variables and no data migration.
It requires an API deployment, but no further reprocessing or reindexing.

Validation: the backend unit/architecture/evaluation run passed 958 tests with
two optional OCR tests skipped. Two subsequently added service integration cases
also pass, checking recovery-to-generation, exclusion of the old rule, absence
of unnecessary web calls, combined token accounting and trace privacy. Backend
mypy and frontend type checking pass; generated API contracts include the new
insufficient-evidence reason. The final focused conversation suite passes 320
tests, including a later search discovering an unresolved amendment to an
earlier dependency.

The new runtime patch has not been deployed or verified live. The verified live
result remains the failed conversation above. Post-deployment acceptance must
use a fresh Test Lab conversation, inspect `knowledge_repair` and source spans,
and rerun the original and explicit-assessment-year questions. A refusal is safer
than the old estimate but does not meet the complete-calculation acceptance test.
Dependency planning and relevance admission still do not constitute general
semantic completeness or legal applicability proof; source-backed amendment
curation and potentially better recall remain necessary if recovery cannot find
the applicable provisions.
