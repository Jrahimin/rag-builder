import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
p=root/'business-upload-log.json'
d=json.loads(p.read_text(encoding='utf-8'))
tests=json.loads((root/'artifacts/business-test-results.json').read_text(encoding='utf-8'))
d['tests']=tests
guide=Path(r'C:\Users\user\Downloads\OmniAskAI-BD-Business-Upload-Guide.md')
d['guide']={'path':str(guide),'sha256':hashlib.sha256(guide.read_bytes()).hexdigest(),'treatment':'Starting map only; its recommendations and filenames were independently checked, not treated as instructions or authority.'}
for i,r in enumerate(d['files'],1):
    assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256'],r['file']
    r['original_file_unchanged_verified']=True
    r['actual_language']='English' if i in (1,2,17,25,26,27) else 'Bangla with English product/standard names' if i in (3,4) else 'Bangla'
    if r['decision'].startswith('uploaded_'):
        assert all(r['ids'].get(k) for k in ('document_id','revision_id','process_job_id','embed_job_id'))
        assert r['metadata']['metadata_persistence_verified']
assert len({r['ids']['document_id'] for r in d['files'] if r['decision'].startswith('uploaded_')})==27
assert len([r for r in d['files'] if r['metadata'].get('lifecycle')=='active'])==d['counts']['active_business_sources']
p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')

lines=[
 '# Bangladesh business-source preparation report',
 '',
 'Audit date: 13 September 2026. Existing project: **Bangladesh Income Tax & Business Legal Guide**.',
 '',
 f"[Open the live project]({d['project_url']}) — project ID `{d['project_id']}`.",
 '',
 '**Outcome:** 29 local files inspected; 27 unique documents uploaded individually. Eight business sources are Active, 19 remain Draft, and two were skipped. All eight original tax sources are preserved. The project is **not yet validated for a complete company-startup guide**, and tax answer verification is **partial**, not a full regression pass.',
 '',
 'The JSON log records each original path and SHA-256, actual title and language, issuing authority, dates and their basis, lifecycle, work identity, source treatment, processing/job/revision IDs, OCR choice, relationships and inspection findings. Every original file remained byte-for-byte unchanged. The Contract Act upload used a verified content-identical unencrypted copy.',
 '',
 '**Project changes and preserved settings**',
 '',
 '- Renamed the existing project and expanded its description to income tax plus business formation and compliance.',
 '- Activated policy revision 10 (`0c58c848…`), appending business scope and evidence-handling instructions to revision 9. Browser readback confirmed that all 5,719 original instruction characters are preserved exactly; the only policy-text change is the 1,533-character addition.',
 '- Preserved the generation model (`openai-gpt-5.6-luna`), authoritative evidence approach, indexed-then-web response mode, balanced assurance, and query translation Off.',
 '- Preserved Custom retrieval: 80 semantic / 80 keyword candidates, Always rerank, window 40, top K 12, context 24, budget 48,000, history 20. Source policy remains Enforce.',
 '- No tax source was uploaded again, deleted, replaced, reprocessed, renamed or given a new metadata revision. All eight immutable revision UUIDs were compared with the baseline and are unchanged.',
 f"- Final recorded source generation: {d['source_generation']}. Index build prefix: `{d['active_index_build_prefix']}`. Source metadata activation and processing/index versions are separate.",
 '',
 '**Active business sources**',
 '',
 '| Source | Publication / effective date stored | Processing and practical limits |',
 '|---|---|---|',
 ]
for r in d['files']:
    m=r['metadata']
    if m.get('lifecycle')!='active':continue
    dt=(m.get('published_at') or 'Unknown')+' / '+(m.get('effective_from') or 'Not assigned')
    lines.append(f"| {m['source_title']} | {dt} | Ready; {r['processing']['pages']} pages; {r['processing']['parser']}. {r['assessment']} |")
lines += ['', '“Not assigned” is deliberate for undated consolidated captures. Original enactment publication is distinguished from the date of amended wording. These sources must not establish historical wording merely because their original Act date is old.', '', '**Draft sources and skipped files**', '', '| Local file | Decision | Reason / unresolved issue |', '|---|---|---|']
for r in d['files']:
    if r['metadata'].get('lifecycle')=='active':continue
    lines.append(f"| {r['file']} | {r['decision'].replace('_',' ')} | {r['assessment']} {r.get('date_basis','')} |")
lines += ['', '**Relationships verified**', '', '| Modifying document | Stored target | Treatment and limits |', '|---|---|---|']
for r in d['files']:
    for rel in r['relationships']:
        lines.append(f"| {r['metadata']['source_title']} | {rel['target_file']} (`{rel['target_revision_id']}`) | Modifies. {rel['operative_scope']}. {rel['scope_status']} |")
lines += [
 '',
 'No Replaces relationship was created. The 2022 and 2026 Labour Rules amendments each point directly to the 2015 Rules. The Labour Act amendment points to the same legal work even though the captured consolidated text already includes its changes; both remain Draft. The DBID duplicate was not uploaded. The 2014 Fire Rules legally repeal the 1961 Rules, but this is not a document-edition replacement: the 1961 document was never stored. Environmental rules implement their parent Act and do not modify it.',
 '',
 'The live editor exposes exact provision-heading scopes. The four Draft modification links are still unscoped in that field: their operative effects are documented, but exact indexed-heading matching has not been verified. No guessed scope was saved to suppress warnings. The two Labour Rules amendment effective dates also remain unset. These are explicit activation blockers, not completed relationship QA.',
 '',
 '**Parsing, indexing and citation checks**',
 '',
 '- All 27 uploaded documents reached Ready with successful process and embedding/index jobs. Each PDF page count matches the local inventory, including all 343 Labour Rules pages and all 107 Environment Rules pages. Markdown/DOCX documents appear as one logical page.',
 '- Explicit Bangla OCR was selected for scans and legacy-font Bangla PDFs. Readable Markdown/DOCX used native extraction. Native English Contract/Partnership and the BSTI compilation used PyMuPDF.',
 '- The live OCR limit was verified as 500 pages, with Google Vision at 200 dpi. A Ready state and matching page count do not certify every OCR character or table cell.',
 '- The encrypted Contract Act was initially rejected before document creation. Removing its empty-password encryption produced 83/83 matching extracted pages and 83/83 matching rendered page images; only that complete copy was uploaded.',
 '- Digital-commerce OCR preserved clauses 3.3.2, 3.4 and 3.5. Its delivery answer passed, but PDF page anchors are missing; section numbers and Gazette page text remain available.',
 '- Environment Rules retrieval returned classification provisions, Schedule 1 and late Schedule 14. Some table serials contain OCR noise; numeric thresholds should be checked against the exact passage/page before use.',
 '- Partnership section 25 was complete locally but truncated in the cited chunk. It was returned to Draft; a subsequent document-filtered search returned no results, consistent with Draft exclusion under Enforce.',
 '- Food Safety initial auto routing selected OCR and split section 43’s number from its body. The full body was retrievable, so it was not an absent-source problem. See its processing history and the post-correction test in the JSON log for final status.',
 '',
 '**Fresh-conversation tests**',
 '',
 'All tests below used new conversations under policy revision 10. “Core rule correct” is distinguished from the application’s complete grounding result; citations alone were not treated as a pass. Test questions, IDs/prefixes, timings and representative citation identifiers are in the JSON log.',
 '',
 '| Test | Backend result | Findings |',
 '|---|---|---|',
 ]
for t in tests:
    claim=f"; {t['claims_supported']}/{t['claims_total']} claims" if 'claims_total' in t else ''
    lines.append(f"| {t['name']} | {t['backend_result']}{claim}; {t['round_trip_ms']/1000:.1f}s | {t['assessment']} |")
lines += [
 '',
 '**Tax preservation conclusion:** configuration and all eight source revisions are unchanged. The Bangla salary test retained the expected gross-salary exclusion and 6,000 investment rebate, giving a 39,000 pre-TDS subtotal for the stated salary-only scenario. However, the mixed-interest English test missed rules, answered in Bangla and remained unverified; the focused Bangla test also remained partially verified. Therefore full tax correctness cannot be confirmed. The pre-existing September 9 acceptance report already recorded a partial mixed-income answer, so the current failures are not evidence that this work changed a tax rule. No attempt was made to retune proven tax settings to conceal these failures.',
 '',
 '**Outstanding gaps before a complete business-startup guide can be claimed**',
 '',
 '1. Obtain complete Companies Act and Labour Act editions with their separate schedules; reconcile current RJSC procedures/forms/fees and clean DNCC/DSCC captures.',
 '2. Resolve DBID publication/commencement chronology, verify the exact application annex against current practice, and finish provision-heading scope mapping.',
 '3. Resolve the Labour Rules amendment effective dates and provision mapping before activating the pack; verify current DIFE procedures against it.',
 '4. Model the bundled BSTI January orders as distinct legal works with separate commencement treatment; verify the seven-product order’s delayed commencement and the current full mandatory-product coverage. The 315-product compilation is not a universal 2026 list.',
 '5. Add/verify the parent Fire Prevention and Extinction Act 2003 and current Fire Rules amendment status; retain the 1961 repeal/savings distinction.',
 '6. Collect food-sector implementing regulations, trademark procedure/rules, and any activity-specific permissions required for the user’s actual business. This corpus is not a complete licensing checklist.',
 '7. Investigate exact-provision retrieval, chunk-boundary/page-anchor defects and claim verification. The broad company-startup test was withheld; it is not a successful end-to-end guide.',
 '',
 '**Independent official corroboration used**',
 '',
 '- [BIDA / Invest Bangladesh FAQ](https://investbangladesh.gov.bd/faq).',
 '- [BSTI mandatory-products index](https://bsti.gov.bd/site/page/741c4948-53e2-455e-bb38-f4281381f426/List-of-Mandatory-Products-); actual local Gazette contents controlled the three-file classification. Some government pages returned gateway errors; those failures were not treated as verification.',
 '- [Current Environment Conservation Act, BDLaws](https://bdlaws.minlaw.gov.bd/act-print-791.html), [Labour Act](https://bdlaws.minlaw.gov.bd/act-print-952.html), [Sale of Goods Act](https://bdlaws.minlaw.gov.bd/act-print-150.html), [Contract Act](https://bdlaws.minlaw.gov.bd/act-print-26.html), and [Trademarks Act](https://bdlaws.minlaw.gov.bd/act-print-1010.html).',
 '- [Food Safety Act and commencement note](https://bdlaws.minlaw.gov.bd/act-print-1127.html) and [BGPress commencement record](https://www.dpp.gov.bd/bgpress/index.php/document/get_extraordinary/12473).',
 '- [Ministry of Commerce digital-commerce notice](https://mincom.gov.bd/pages/notices/6940322335ce18e1c055a68a) and [BGPress Gazette record](https://www.dpp.gov.bd/bgpress/index.php/document/get_extraordinary/40432).',
 '- [Fire Service laws/rules page](https://fireservice.gov.bd/pages/static-pages/6922dc84933eb65569e10c58); the actual repeal was independently read in the 2014 local Gazette, rule 24, PDF page 11.',
 '',
 f"Detailed machine-readable audit: [business-upload-log.json]({(root/'business-upload-log.json').as_posix()}).",
 ]
(root/'business-upload-report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Report written; tests:',len(tests),'; originals unchanged:',len(d['files']))
