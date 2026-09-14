# Bangladesh company-law readiness audit — 14 September 2026

**Readiness is not certified. The backend is available again and the earlier wording fix is verified live.** Six fresh conversations completed after deployment. The broad dormant-company queries still fail evidence-review validation, while focused statutory retrieval improves. New citation and deadline-verification fixes are tested locally and await deployment. No tax source or proven setting was changed.

The final checked inventory at source generation101 has38 ready documents:19 Active and19 Draft. Business sources comprise11 Active and19 Draft; the8 tax sources remain as found. The policy is still revision10, ID `0c58c848-3c5e-4796-a5cc-736a0aaf7d6c`, effective hash `94941a1a3bd5`, resolution `af420b288290`.

## Changes and decisions

- Inspected all35 baseline sources live and the metadata, relationships and processing facts of all27 business documents. All17 Bangla display-title corrections were saved and rechecked in the final inventory. Exact prior Bangla titles were retained in revision reasons; existing document language, dates, roles and relationships were preserved.
- Corrected English Companies Act source `50415d8d-3102-4ae6-9d7b-83ae10089cbf` to Draft r2 and cleared its erroneous1932 publication date. Its90pages end at section404 and omit schedules.
- Acquired the complete official schedules and2019 ScheduleII replacement Gazette. A256page combined reading PDF was uploaded and tested, then returned to Draft r3 because Bangla retrieval substituted model articles for statutory section81. It remains available as an audit artifact, not active answer authority.
- Uploaded and activated a complete two-part reading bundle: native statutory text `5ed77c17-926b-4d70-830f-903c039c4e68` (418 provision blocks and all official amendment notes) plus schedules `23572ffb-535a-4929-90c7-3f6e21fffb6f` (125pages including provenance cover). Both are r2, primary, ready. Same work identity `bd.business.companies-act-1994`; complementary parts, not amendments or replacement edges. Native headings improved Bangla retrieval of sections81 and36. Activation is not a claim of complete answer readiness.
- Confirmed Partnership Act section25 is complete in the local PDF and live page8 chunk57 (`969add3c`). Existing Active r4 was retained; earlier clipping was a preview issue.
- The prior Bangla Act `30e9188a-53d9-4810-9985-30628d5d4687` was absent from the live inventory. This audit did not delete it.

## Official sources and currency

The native Act comes from [BDLaws current statutory text](https://bdlaws.minlaw.gov.bd/act-print-788.html); original publication1994-09-12 and commencement1994-12-01 are distinguished from consolidated amendment dates. [Official schedules](https://bdlaws.minlaw.gov.bd/upload/act/2020-12-30-16-34-32-788___Schedule.pdf) include the2020 OPC additions. Original ScheduleII pages were excluded and replaced by SRO101-Law/2019 from [RJSC fee Gazettes](https://roc.gov.bd/pages/files/6922da04933eb65569e01dcf), effective2019-04-23. These are explicitly source-preserving reading assemblies, not newly issued official consolidations. Component hashes, page boundaries and QA are in the manifests.

The [official registration page](https://roc.gov.bd/pages/static-pages/6922dd32933eb65569e13e40), updated2025-01-26, rendered only its heading. The [forms catalogue](https://roc.gov.bd/pages/forms/6922d9b8933eb65569dffa1c), updated2016-08-08, links28 PDFs; seven core forms were downloaded locally. FormVI still prints Tk20 filing fees, so the old prints are not current fee authority. No new forms were activated.

The [live RJSC calculator](https://app.roc.gov.bd/psp/fee_calculator) was tested with Registration / Private Company / authorised capital Tk1,000,000. It displayed registration0, filing1,200, MOA/AOA stamps12,300, certified copy/certificate1,520 and “15% VAT will be added.” This is a single scenario; VAT base and a universal total were not inferred.

## Why the supplied API answer failed

The API envelope succeeded, but final evidence selection returned `insufficient_evidence / unresolved_authority`:11 relevant passages, zero final passages,12 recovery searches and no final generation. Its91.935second latency included six preparation LLM calls. Missing company/RJSC/VAT coverage and unresolved existing tax amendment scopes prevented a supported answer; web fallback was deliberately suppressed. The protected tax relationships were not altered or bypassed.

Live tests found additional problems: combined OCR blurred statutory/schedule context; page-qualified citation syntax such as `[1, পৃষ্ঠা ১]` is not matched by the grounding service’s bracketed-digit pattern; and broad applicability recovery can still withhold an answer. The native component fixes retrieval structure but does not fully fix these downstream issues.

The local bilingual refusal-wording correction in `backend/app/modules/conversations/services/chat_service.py` passed8focused tests and Ruff checks. It is **now verified deployed** in both English and Bangla refusals. It does not repair missing authority or guarantee legal correctness.

## Fresh conversation tests

| Test | Conversation ID | Result | Supported claims |
|---|---|---|---|
| tax_en_registration | e8a77330-87ce-4fa2-b1a3-891c5ccc61b3 | grounded | 3/3 |
| tax_bn_registration | b7802d75-d164-4a6d-9cd1-4325c44aca6e | partial | 6/8 |
| company_en_agm_schedule_x | 1e48f7cd-440c-4b69-8fa6-9a97bcb3b22a | partial | 23/26 |
| company_bn_agm_schedule_x | 918c176d-8f72-444c-a854-0f9ea441aa75 | failed_material_legal_error | 0/8 |
| tax_en_calculation | fc016038-c267-4e18-a8e3-a42c932b8b60 | partial | 11/22 |
| tax_bn_calculation | 164d3366-4c6a-4864-a8c6-54f25a2f0d7d | partial | 11/27 |
| structured_company_bn_same_query | a09dfe40-fc5b-4b27-aed6-4fdbdd2cefe1 | substantive_deadlines_improved_citation_format_failed | 0/6 |
| structured_company_en_same_query | 3b01e34b-906d-4a3a-acbd-9720577ba873 | insufficient_evidence | — |
| structured_company_bn_citation_format | 37507c41-8f39-474e-833f-880fcb466866 | partial | 2/3 |
| structured_company_en_citation_format | e70bb5b7-0412-4d21-8109-b4df9cb058a8 | partial | 2/3 |
| original_company_en_retest |  | submitted_result_unavailable_backend_outage | — |
| original_company_bn_retest |  | submitted_result_unavailable_backend_outage | — |

Company statutory retrieval was checked at Act section81, section36, ScheduleX, ScheduleXII and replacement ScheduleII. After native restructuring, the same Bangla query retrieved section81 chunk97 (`f5c92c5f`) and section36 chunk49 (`9c5daade`). An additional test deliberately/incorrectly referenced36(4); English corrected it to36(3), whereas Bangla did not. This is recorded as a premise-handling limitation, not a passing legal test. Tax registration in English passed, but bilingual tax calculations remained partial. **A full tax regression pass is not claimed.**

Fresh bilingual conversations were not completed individually for every title-only change. Metadata/lifecycle persistence was checked for those changes; the requested full per-source answer suite remains incomplete.

## Source decisions

The detailed JSON retains dates, hashes, IDs, processing choices, previous revisions and relationships. The following table includes the historical missing source explicitly; it is not counted in the live38.

| Source ID | Display title | Lifecycle / inventory | Decision reason |
|---|---|---|---|
| 53653d4d-4d0f-44ec-aa21-59c41ad7aa78 | BIDA / Invest Bangladesh — Business and Investment FAQ (English) | draft | Undated FAQ capture matched to official FAQ. Question headings omitted and form boilerplate retained; not ready for activation. |
| 26213814-6529-4aa2-a3ca-60a381d7db71 | BSTI — List of 315 Mandatory Products (English; 2025 compilation) | draft | Complete ten-page list, items 1–315. Supporting compilation, not an operative SRO; does not represent all 2026 additions. |
| 036208fb-87cf-48de-a9c1-6c662e8f009b | BSTI — SRO 33-Law/2026 and SRO 34-Law/2026: 13 products, standards and certification marks (Bangla) | draft | Two complete distinct orders for the same 13 products. SRO 33 section 21(1) standards prohibition; SRO 34 section 21(2) certification mark obligation. Not duplicate or successor list. Draft bundle requires separate work/date modelling. |
| 5670cd56-a5a0-4969-b877-8bafeb605203 | BSTI — SRO 232-Law/2026: standards requirement for seven additional products (Bangla) | draft | Complete SRO 232 with seven products: fortified maize oil, man-made saris/fabrics, LED luminaires, plastic feeding bottles, toys and AAC blocks. Independent standards requirement, not replacement of compilation. |
| 30e9188a-53d9-4810-9985-30628d5d4687 | কোম্পানী আইন, ১৯৯৪ — amended Bangla text; schedules absent | absent_not_deleted_by_this_audit | Act 18/1994; sections through 404, 33 amendment notes including 2005/2015/2020 and OPC provisions. Separate schedules absent; incomplete consolidated capture remains Draft. |
| 201bdcc8-f331-4d94-be1c-be99807edf78 | DBID Registration Guidelines 2022 — Paragraph 4.1.2 Amendment Circular (18 June 2023) | draft | Signed circular changes paragraph 4.1.2 landlord NID requirement to landlord NID OR electricity-bill copy OR rental agreement. Actual amendment, not application instructions. Draft with unresolved base package chronology. |
| c791a8cc-c8b8-49cf-82c8-1102d24f00b8 | Digital Business Identity (DBID) Registration Guidelines, 2022 — Signed Package | draft | Complete signed 15-page package, forwarding letters, operative guideline and application annex. Scan, not landing page. |
| 627b76cb-65c9-459d-9132-fe56a8da944a | DIFE — Licensing, Renewal and Layout Approval FAQ (Bangla; undated) | draft | Seven-page licensing, renewal, amendment and layout FAQ; six licence types, forms 76/77 and fee tables. Undated; relies on Labour Rules 2015. Draft pending currency and rules-pack checks. |
| 8bbc2453-a628-466e-9e5f-f89e52611f52 | Digital Commerce Operation Guidelines, 2021 | active | Complete eight-page operational guideline through 3.5; Bangla OCR delivery clause 3.3.2 and other clauses readable. Fresh Bangla citation test passed. Citation page anchors missing although section text and Gazette page text survive. |
| f5cbfdfb-3aa6-4d92-8e7e-95e3c602af82 | DNCC — Trade Licence Issuance and Renewal Procedure (17 July 2025) | draft | Two-page webpage capture; right edge of first page is severely clipped. OCR cannot restore missing content. Draft pending clean recapture. DNCC only. |
| bcb74cd8-eb26-4610-af62-f2816f0f373f | DSCC — Trade Licence Issuance and Renewal Procedure (28 January 2025) | draft | Two-page procedure; source itself misaligns time/fee/law rows. Draft pending reconciliation; DSCC only. |
| 2bb90c33-e976-417d-b889-b2744b98767f | Bangladesh Environment Conservation Act, 1995 — Consolidated Text | active | Act 1/1995 current consolidated provisions, penalty table, 28 amendment notes including 2010; official BDLaws Act 791 corroborated. Historical territorial commencement outside capture. |
| 6aee09d1-1599-4046-9e2a-519e87517864 | Environment Conservation Rules, 2023 — SRO 53-Law/2023 | active | Complete 107-page Gazette with schedules/classifications/forms. Implements Act 1995; does not modify the Act. Bangla OCR classification passages and late Schedule 14 retrieved. Some serial-number OCR noise; individual threshold answers still require exact passage review. |
| abcc16c0-51b1-4f04-8cf5-a5da134989ca | Fire Prevention and Extinction Rules, 2014 — SRO 230-Law/2014 | draft | Complete 72-page rules/forms/schedules. Rule 24 (PDF page 11) expressly repeals 1961 Rules with savings. Parent 2003 Act missing; commencement/amendment currency unresolved; Draft. Official amendment proposal is not proof of an enacted amendment. |
| ca586bbc-9c0d-42e7-a174-262667e0bc13 | The Food Safety Act, 2013 — Authentic English Text (SRO 98-Law/2016) | active | Complete 47-page authentic English translation with offence/penalty schedule. Explicit English reprocessing restored native extraction and complete section 43 with page 24 citation. Legacy-font running headers still contain noise; broad-query retrieval remains variable. Sector implementing regulations not collected. |
| 9e2ca5ca-8f7d-4829-9a41-22e8e1a63f0c | Bangladesh Labour Act, 2006 — Consolidated Through 2026 (Schedules Missing) | draft | Sections through 354 with 399 amendment notes, including Act 43/2026 already incorporated. Separate schedules absent. Draft incomplete consolidated text; do not double-apply 2026 amendments. |
| b6f93c98-5127-4d80-b6b7-40d2c4ed696d | Bangladesh Labour (Amendment) Act, 2026 — Act 43 of 2026 | draft | Complete 47-page Act 43/2026 through section 93, repeal/savings of 2025 Ordinance. Modifies stored Labour Act; base already incorporates changes but lacks schedules. Pack remains Draft. |
| d5361d1a-bb63-4f21-afba-782b89e2b2af | Bangladesh Labour Rules, 2015 — Amendment SRO 284-Law/2022 | draft | 24 pages; SRO 284-Law/2022, 101 amendment items and forms. Directly amends 2015 Rules. Draft until effective date and provision-level scope reconciled. |
| 483bdc35-139a-48e3-9a39-3f6542f965c2 | Bangladesh Labour Rules, 2015 — Amendment SRO 53-Law/2026 | draft | Seven pages; SRO 53-Law/2026 inserts rules 226ক–226ঠ after rule 226 for oil/gas-sector central fund. Not a general leave amendment. Directly modifies 2015 Rules, not 2022 amendment. |
| c724d392-fff5-4910-8c72-92952127cf0e | Bangladesh Labour Rules, 2015 — Base Rules, SRO 291-Law/2015 | draft | Complete 343-page base rules with forms/annex and final signed page. All 343 pages reported by live OCR, within verified 500-page limit. Draft with unresolved amendment pack; Ready is not certification of every OCR cell. |
| 39ac4bf0-0763-42f6-a03f-4eff5911d2c1 | Importers, Exporters and Indentors (Registration) Order, 2023 — SRO 326-Law/2023 | draft | Complete 16-page registration order for importers, exporters and indentors, including forms/fees. No stored predecessor; no replacement inferred from name/year. |
| 241c687a-144b-41a1-b18a-5f59518e8ea5 | RJSC — Entity Registration Procedure (Undated Capture) | draft | Native Bangla entity-specific procedures/forms. Undated and refers to former Board of Investment; conflicting private-company name-clearance wording. Draft pending current official corroboration. |
| fa618b01-358e-4b64-9818-54895de4a8bc | The Contract Act, 1872 — Bangladesh amended text (English) | active | Complete adapted English Act, contents, sections through 238, repeal notes and schedule. Uploaded content-identical unencrypted copy after original rejection. Exact section 10 exists in index (page 19, chunk 33); multi-rule chat retrieval missed it. |
| e01531e9-a216-4c18-87bf-7517d92fb949 | The Partnership Act, 1932 — Bangladesh amended text (English) | active | Live r4 retained: full section25 is present in page8 chunk57. Earlier truncation was a preview issue. |
| 11966bf3-d39e-4b2c-a539-33c7ca948f09 | The Sale of Goods Act, 1930 — Bangladesh amended text (English) | active | Complete native English sections 1–66 including 64A and adaptation notes. Official BDLaws Act 150 corroborated. Section 16 fitness exception retrieved/cited correctly. |
| af30a67a-671a-44a8-8669-65abb51a062b | Trademarks Act, 2009 — Consolidated Including 2015 and 2023 Changes | active | Act 19/2009, full Bangla sections 1–128 and 14 amendment notes, including Act 23/2015 and Industrial Design Act 22/2023 section 36 substitutions. Official BDLaws Act 1010 corroborated. Rules 2015 missing, separate procedural gap. |
| eca42359-9272-422c-b4d3-9b50ed7fdddb | Consumers Right Protection Act, 2009 | active | Act 26/2009, complete Bangla sections through 82. Bangla prevails. Section 60 complaint limitation retrieved, quoted and verified in fresh Bangla test. |
| 50415d8d-3102-4ae6-9d7b-83ae10089cbf | Companies Act, 1994 — English Text (Schedules Missing; Amendment Currency Unverified) | draft | See detailed audit record |
| 5e65a99d-09e5-494d-ab44-5de8d00d4042 | Companies Act, 1994 — Consolidated Body and Schedules I–XII (Official Components) | draft | See detailed audit record |
| 5ed77c17-926b-4d70-830f-903c039c4e68 | Companies Act, 1994 — Part A: Complete Statutory Sections (Official Bangla) | active | See detailed audit record |
| 23572ffb-535a-4929-90c7-3f6e21fffb6f | Companies Act, 1994 — Part B: Schedules I–XII and Model Articles (Official Components) | active | See detailed audit record |

## Unresolved work

- Broad dormant-company and incorporation readiness is not certified: applicability/authority recovery still withholds some answers.
- Current RJSC registration page contains no rendered substantive procedure; old local capture remains Draft. Complete current procedures, all forms and operational fee rules are not fully ingested.
- Seven official forms downloaded locally, not uploaded; legacy fee labels and form extraction/current-applicability issues prevent blanket activation.
- Native statutory/schedule separation improves retrieval but does not yet yield fully verified bilingual company-law answers; citation formatting and source/page attribution require further remediation.
- Company nil-return, VAT/BIN, activity-specific licence applicability and consequences remain insufficient for comprehensive dormant-company advice.
- Existing tax amendment scopes/effective metadata remain unresolved. User-protected tax sources/settings untouched; fresh tax calculations returned partial results.
- Existing Draft labour, DBID, fire, BSTI and registration-order sources retain documented date/scope/parent-law/currency issues; do not infer broad licence requirements.
- Local bilingual refusal wording fix passed8focused tests but is not deployed.
- Fresh conversations for every title-only metadata change individually have not been completed.

## Resumed post-deployment validation

Live source generation remains **101**, with **38 ready documents (19 Active / 19 Draft)**. Active build prefix is `d0317f95`; conversation policy remains revision **10**, authoritative, translation Off. Earlier outage observations above are historical.

| Fresh test | Conversation ID | Result | Claims supported | Time |
|---|---|---|---|---|
| original_company_en | `015781b4-df52-4e55-9c35-44e76e344021` | refused | No final generation | 64.324s |
| original_company_bn | `38f948fa-b6be-43fd-8289-0fc4c04f8dde` | refused | No final generation | 88.143s |
| companies_sections_en | `eaa0a79f-5bce-4227-8868-7fd1cb1b8e28` | grounded | 8/8 | 9.281s |
| companies_sections_bn | `46fdca0c-6cad-44bd-a67a-5a04812df230` | partial | 11/13 | 58.966s |
| tax_registration_en | `f8e218f4-c4eb-4005-84fb-e9910e4351eb` | partial | 2/3 | 7.996s |
| tax_registration_bn | `6f3b676c-4ae5-4434-99d5-47bcbc47ad5c` | partial | 2/3 | 6.425s |

The English broad query admitted 9 passages and Bangla admitted 12; both selected zero final passages. Recovery failed with `invalid_model_response`: English returned invalid JSON; Bangla returned an invalid or blank source-line range after the existing format retry. Thus these runs expose a structured reviewer-output failure as well as unresolved legal coverage. Exact source verification and authority gates were not bypassed. The reviewer-output failure remains unresolved; the new citation fix runs later and cannot fix a refusal before generation.

The focused English statutory query passed 8/8 claims. The Bangla answer used the correct sections 81 and 36(3), but lacked the comparative schedule evidence and remained partial (11/13). Both tax registration answers returned the expected topic, yet only 2/3 claims were supported. Bangla review marked the short TIN list fragment unverified. This is not a fully passing tax regression or a new calculation certification.

### New local fixes awaiting deployment

- Page-qualified citation markers such as `[1, পৃষ্ঠা ১]` and grouped markers are recognized only when every page matches the retrieved chunk's page number. Unknown pages, mismatches, out-of-range references and ambiguous syntax are not normalized. Content verification remains mandatory.
- A changed day/month/year quantity cannot be marked supported solely through similar wording when that quantity is absent from cited evidence. The regression `90 days` versus a source's `21 days` now remains unverified. Bangla digits and units are handled without guessed conversions.
- The existing single source-range correction retry now receives the offending source label, line count and nonempty line numbers, rather than an ambiguous range-only error. Repeated invalid ranges remain blocked. This improves retry guidance; it does not prove the live reviewer failure is resolved.

Validation: **281 tests passed** (199 grounding/chat and 82 evidence-repair), including 10 new citation/deadline cases and repeated-invalid-range rejection; Ruff lint, formatting and diff checks passed. These edits are in `backend/app/modules/conversations/grounding_service.py`, `services/evidence_repair_service.py` and their unit tests. They are **not deployed** and have not been represented as live fixes.

### Official-source rechecks and remaining decisions

The [RJSC registration page](https://roc.gov.bd/pages/static-pages/6922dd32933eb65569e13e40) now renders its full body, updated 26 January 2025. This supersedes the earlier heading-only observation. However, it says private companies are excluded from pre-registration name clearance while requiring name clearance in the private-company checklist. The existing source `241c687a-144b-41a1-b18a-5f59518e8ea5` remains Draft until that conflict is resolved against the current workflow. No live metadata revision was saved: the Projects selection UI continued to display the tax correction form after the RJSC click, and that form was left untouched.

[Official fee-calculator help](https://app.roc.gov.bd/help/fee_calculator.htm) explains input selection but supplies no fee schedule, VAT base or effective date. It does not resolve the legacy form-fee/current-calculator conflict. Existing official forms and licence/compliance drafts remain subject to the earlier completeness and currency decisions.

Remaining work: repair and verify structured coverage-review reliability; deploy and live-test the new citation/deadline guard; complete current RJSC workflow/forms/fees and relevant licence/VAT evidence; resolve protected tax authority gaps without changing proven settings; obtain consistently supported bilingual answers. Full company-law readiness remains **false**.
