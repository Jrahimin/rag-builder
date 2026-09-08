# Partial-answer implementation and local validation — 9 September 2026

Implemented locally following the live smoke review. No live code deployment, project
configuration, source metadata, index or model was changed. The pre-existing smoke-review
document is preserved.

## Runtime changes

- Authoritative recovery v17 uses stable rule requirement IDs rather than treating every
  alternate-language search as another rule. Coverage can classify personal versus source
  gaps even when source checks fail, without another classification call. Unclassified or
  mixed gaps remain source gaps; classification never independently authorizes generation.
- An incomplete review may identify useful independent work. Every selected requirement
  must pass the existing exact source proof; missing/duplicate/unsupported IDs fail closed.
  The original coverage remains incomplete. Only selected proof passages reach generation.
  Generation receives supported requirement descriptions, exclusions and pending gaps;
  free-form scope prose is not passed as a source of additional rules.
- Generation must label the result partial, name excluded supplied components, and avoid
  combined totals whose rates, limits or inputs depend on unresolved components. This is
  still an LLM separability judgment, not a formal proof of every legal interaction.
- Claim inspection adds input, assumption, arithmetic, refinement and source-assertion
  categories. These conservative text categories are diagnostic and can miss paraphrases
  or contain mixed assertions. They do not exempt claims from existing source checks.
- Separate decimal arithmetic diagnostics handle explicit percentage products and simple
  addition/subtraction equations. Incorrect arithmetic fails verification; correct
  arithmetic does not promote an unverified source assertion. Unsupported expressions,
  implicit table totals and multi-step provenance remain unverified.
- Refusals now display “Answer withheld” and “Task remains unanswered”, with specific
  coverage gaps. Neither a refusal nor a partial answer is shown as successful task
  completion, including historical results with a saved passed flag.

## Browser smoke

Local conversation `3bb7751a-be16-4d42-95c4-beb00673b8cf`, one attempt, used the salary,
investment and two interest amounts from the original review, plus an explicit request
to return independently supported work when a component remains unresolved. This was
not a verbatim replay of the original question.

Observed configuration: local project `2ee2756f-ad27-44df-a9d3-1316b10ccbb1`, revision 36,
source generation 22, authoritative approach, translation off, `gpt-5.6-luna`, and recovery
`v17-authoritative-partial`. Local source generation differs from the supplied live run;
the two environments are not an equivalent benchmark.

The answer returned the salary-income intermediate amount of BDT 800,000, explicitly
excluded unresolved interest/rebate/combined tax work, and did not claim final liability.
The console displayed “Partial answer”, “Needs Attention”, evidence coverage `partial`,
and 3 of 22 claims supported. This validates the partial-answer delivery path and its
presentation, not the legal correctness of the answer or complete task satisfaction.

Browser round trip: **129.364 seconds**. Five completed LLM calls, 11 reranks, 24 embedding
calls; coverage/recovery 95.242 seconds and generation 14.839 seconds. LLM input plus
output tokens total **95,122**. Stage durations overlap. This run does not establish a
latency improvement. Recovery used ten searches and two coverage reviews before a useful
partial scope was accepted.

The smoke exposed a sentence-final punctuation miss in the arithmetic parser and scope
prose mentioning a rule outside its selected IDs. Both were corrected and regression
tested afterward; these final refinements were not subjected to another provider call.

## Interest evidence audit

Two read-only searches in the live console recovered these concrete source locations:

| Evidence | Location | Audit significance |
| --- | --- | --- |
| Income Tax Act 2023 Bangla | PDF page 61, chunk 58, prefix `eb8e042e`, characters 94364–96065 | Contains sections 62–65: financial-asset income classification, timing and deductions. The passage explicitly includes bank deposits and financial products/schemes. |
| Nirdeshika 2026–27 | PDF page 54, chunk 92, prefix `c3959506`, characters 93363–95107 | Current-period guidance refers to sections 62–65 and expressly includes savings certificates in its securities description. This is governing explanatory text, not a worked example. |
| Ordinance 52 of 2025 | PDF page 2, chunk 4, prefix `15224a75`, characters 2580–3900 | Section 4 replaces section 163(11); its table includes savings-certificate profit under section 105 for natural persons. |
| Paripatra 2026–27 | PDF page 95, chunk 118, prefix `3fdea9bf`, characters 141404–143063 | Item 61 describes the Finance Act 2026 replacement of section 163, refundable excess Part 7 tax, and special treatment under sections 138–139. This requires reconciliation with the older section 163(11) passage. |
| Income Tax Act 2023 Bangla | PDF page 83, chunk 85, prefix `872e6ab7`, characters 135872–137571 | Base section 105 withholding provision. By itself it does not establish current final-tax or credit treatment. |

The live searches used active build prefix `adca16b6`. Inclusion evidence exists in the
corpus, although search frequently ranks worked examples above it. The next authority
audit should reconcile the actual replacement section 163 and its commencement against
the earlier section 163(11), then verify sections 102/105 and payment-credit provisions
for the chosen assessment year. Link actual replaced provisions, not product-name matches.

No metadata correction was justified solely by these search excerpts. The complete
amendment chain and all current credit/exemption exceptions remain unverified. The local
smoke also still reports two unscoped modifier relationships and one incomplete metadata
relationship. The answer's unnecessary location-specific and investment-eligibility gaps
show that coverage classification still needs further corpus-backed calibration.

## Validation

Conversation and architecture suites: 479 tests passed before the final narrow regression
additions. The final affected recovery/prompt/arithmetic suite passes **95 tests**. Frontend
message/search tests pass **21 tests**. TypeScript, Ruff, mypy on the five affected core files, and diff
whitespace checks pass. No migration or reindex is required for these runtime changes.

Remaining work: authenticate the full provision chain before metadata edits; improve
coverage stability and claim provenance beyond diagnostic categories; measure latency
on comparable snapshots. Live deployment and live acceptance of these code changes have
not been performed.
