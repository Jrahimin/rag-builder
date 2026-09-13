import json, hashlib, re
from pathlib import Path
from datetime import datetime, timezone
import pymupdf
root=Path.cwd(); out=root/'artifacts/company-readiness'; out.mkdir(exist_ok=True)
logpath=root/'business-upload-log.json'; log=json.loads(logpath.read_text(encoding='utf-8'))
raw=Path(r'C:\Users\user\.codex\attachments\f8771ae4-a3e3-4cd3-a41c-6a29440dae8f\pasted-text.txt')
r=json.loads(raw.read_text(encoding='utf-8')); a=r['data']['assistant_message']; m=a['metadata']; repair=m['knowledge_repair']
now=datetime.now(timezone.utc).isoformat()
diagnostic={
 'examined_at':now,'input_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
 'project_id':a['project_id'],'conversation_id':a['conversation_id'],'message_id':a['id'],
 'question':r['data']['user_message']['content'],'response':a['content'],
 'finish_reason':a['finish_reason'],'insufficient_evidence_reason':a['insufficient_evidence_reason'],
 'source_metadata_generation':a['source_metadata_generation'],'index_build_id':a['index_build_id'],
 'config_snapshot_id':a['config_snapshot_id'],'retrieved_chunk_count':m['retrieved_chunk_count'],
 'selected_chunk_count':m['selected_chunk_count'],'generation_time_ms':m['generation_time_ms'],
 'total_latency_ms':a['total_latency_ms'],'lifecycle':m['lifecycle'],
 'evidence_gate':m['evidence_gate'],'evidence_funnel':m['evidence_funnel'],
 'web_search':m['web_search'],'current_authority':m['current_authority'],
 'repair_summary':{k:repair.get(k) for k in ['status','trigger','queries','requirements']},
 'repair_missing':repair.get('coverage',{}).get('missing',[]),
 'live_replay_status':'blocked_browser_sandbox_and_unauthenticated_API',
 'interpretation':[
 'API success true is transport/application success, not successful grounded answering.',
 'Relevance admission accepted 11 passages, but recovery failed current-authority and full coverage checks; final selection was empty.',
 'Two unscoped active tax modifications and one incomplete-metadata modifier were recorded. Stale revisions were excluded and must not be treated as current operative amendments.',
 'Company/RJSC continuing obligations, company nil-return rules, VAT/BIN and default consequences remained unsupported after 12 recovery queries.',
 'Generation never ran. The recorded six LLM calls and 155349 input tokens belong to preparation/recovery activity, not a successful final answer.',
 'Web fallback was deliberately suppressed for unresolved authority; no endpoint transport failure or model timeout is established.',
 'Do not disable authority checks or change tax settings to make this query pass.'
 ]}
(out/'failed-query-diagnosis.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
titles={
 'Company Act, 1994 (bangla).md':'Companies Act, 1994 — Amended Bangla Text (Schedules Missing)',
 'DBID amendment 2023 (bangla).pdf':'DBID Registration Guidelines 2022 — Paragraph 4.1.2 Amendment Circular (18 June 2023)',
 'DBID Guidelines 2022 (bangla).pdf':'Digital Business Identity (DBID) Registration Guidelines, 2022 — Signed Package',
 'Digital Commerce Guidelines 2021 (bangla).pdf':'Digital Commerce Operation Guidelines, 2021',
 'DNCC trade licence procedure.pdf':'DNCC — Trade Licence Issuance and Renewal Procedure (17 July 2025)',
 'DSCC trade licence procedure.pdf':'DSCC — Trade Licence Issuance and Renewal Procedure (28 January 2025)',
 'Environment Act, 1995 (bangla).md':'Bangladesh Environment Conservation Act, 1995 — Consolidated Text',
 'Environment rules 2023 (bangla).pdf':'Environment Conservation Rules, 2023 — SRO 53-Law/2023',
 'Fire Service Rules, 2014 (bangla).pdf':'Fire Prevention and Extinction Rules, 2014 — SRO 230-Law/2014',
 'Labour Act 2006 (bangla).md':'Bangladesh Labour Act, 2006 — Consolidated Through 2026 (Schedules Missing)',
 'Labour Amendment Act 2026 (bangla).pdf':'Bangladesh Labour (Amendment) Act, 2026 — Act 43 of 2026',
 'Labour Rules amendment 2022 (bangla).pdf':'Bangladesh Labour Rules, 2015 — Amendment SRO 284-Law/2022',
 'Labour Rules amendment 2026 (bangla).pdf':'Bangladesh Labour Rules, 2015 — Amendment SRO 53-Law/2026',
 'Labour-Rules-2015.pdf':'Bangladesh Labour Rules, 2015 — Base Rules, SRO 291-Law/2015',
 'Registration Order, 2023 (bangla).pdf':'Importers, Exporters and Indentors (Registration) Order, 2023 — SRO 326-Law/2023',
 'RJSC registration procedure (bangla).docx':'RJSC — Entity Registration Procedure (Undated Capture)',
 'Trademark Law, 2009.md':'Trademarks Act, 2009 — Consolidated Including 2015 and 2023 Changes',
 'vokta adhikar law 2009 (bangla).md':'Consumers Right Protection Act, 2009',
}
auditrows=[]
for item in log['files']:
 p=Path(item['path']); oldtitle=item.get('metadata',{}).get('source_title')
 row={'document_id':item.get('ids',{}).get('document_id'), 'file':item['file'],
 'baseline_revision_id':item.get('ids',{}).get('revision_id'),
 'live_metadata_checked':False,'baseline_lifecycle':item.get('metadata',{}).get('lifecycle'),
 'proposed_display_title':titles.get(item['file'],oldtitle),'title_change_applied':False,
 'source_language_to_retain':item.get('actual_language'),
 'original_title_to_preserve':oldtitle,
 'official_title_verification':'Verify exact official heading against source before writing; baseline title can include editorial qualifiers.',
 'decision':'preserve_previous_decision_pending_live_verification',
 'reason':item.get('assessment'), 'local_present':p.exists()}
 if p.exists(): row['local_sha256_matches_baseline']=hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
 item['company_readiness_preflight_2026_09_14']=row
 auditrows.append(row)
p=Path(r'C:\Users\user\Downloads\BD Business Docs\Company Act, 1994 (english).pdf'); doc=pymupdf.open(p)
newfile={'file':p.name,'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,
 'pages':len(doc),'document_id':None,'status':'local_only_not_uploaded','recommended_lifecycle':'draft',
 'proposed_display_title':'Companies Act, 1994 — English Text (Schedules Missing; Amendment Currency Unverified)',
 'source_language':'English','official_title_as_printed':'The Companies Act (Bangladesh), 1994',
 'authority_claim_as_printed':'SRO 177-law dated 1-10-95; Ministry of Commerce; not independently authenticated in this audit',
 'pdf_creation_metadata':doc.metadata.get('creationDate'), 'legal_date_assigned':False,
 'finding':'90 pages; final page ends at section 404; no appended schedules. PDF metadata identifies a 2008 file creation, not a legal publication/effective date. Later amendment coverage not established.',
 'qa':'Native text inspected at boundaries and schedule headings searched; rendered first/last pages, but visual viewer also blocked by sandbox. Not a complete visual or legal review.',
 'relationship_decision':'Potential parallel-language text of the same work, not an amending instrument; do not create MODIFIES or REPLACES from translation alone.'}
probes=[
 ('company_en_dormant',diagnostic['question']),
 ('company_bn_dormant','বাংলাদেশে একটি প্রাইভেট লিমিটেড কোম্পানি নিবন্ধিত হয়েছে কিন্তু ব্যবসা শুরু করেনি। এর বার্ষিক সভা, রিটার্ন, নিরীক্ষা, রেজিস্টার, কর ও ভ্যাটের কী বাধ্যবাধকতা আছে? কর্তৃপক্ষ, সময়সীমা ও প্রযোজ্য বিধান উল্লেখ করুন।'),
 ('company_en_incorporation','Explain incorporation of a Bangladesh private limited company, including current RJSC forms, fees and applicable schedules. Distinguish incorporation from activity-specific licences.'),
 ('company_bn_schedules','কোম্পানী আইন, ১৯৯৪-এর তফসিল-১, তফসিল-২ ও তফসিল-১০ কী কাজে লাগে? মূল উৎসের নির্দিষ্ট অংশ উদ্ধৃত করুন।'),
 ('tax_en_regression','For assessment year 2026-27, calculate income tax for a resident male taxpayer under 65 with annual gross employment income BDT 1,200,000 and eligible investment BDT 200,000, no other income and no tax deducted. Cite the salary exclusion, slabs and rebate rules.'),
 ('tax_bn_regression','২০২৬-২৭ করবর্ষে ৬৫ বছরের কম বয়সী নিবাসী পুরুষের বার্ষিক মোট চাকরির আয় ১২,০০,০০০ টাকা, যোগ্য বিনিয়োগ ২,০০,০০০ টাকা, অন্য আয় ও উৎসে কর নেই। চাকরির আয়ের অব্যাহতি, করহার ও রেয়াতের বিধান উদ্ধৃত করে কর হিসাব করুন।')]
webrefs=[
 {'url':'https://bdlaws.minlaw.gov.bd/act-print-788.html','finding':'Official current consolidated Act lead; schedules and complete amendment set still require verification.'},
 {'url':'https://roc.gov.bd/pages/forms/6922d9b8933eb65569dffa1c','finding':'Official form catalogue includes Schedule X; page content dated 2016, not proof of current fees or every form version.'},
 {'url':'https://roc.gov.bd/site/page/855dc577-3035-4ca4-b376-49c517099a3e/প্রতিষ্ঠান-নিবন্ধন','finding':'Official entity registration page indexed with content update 2025-01-26; requires full capture and currency check.'},
 {'url':'https://app.roc.gov.bd/help/fee_calculator.htm','finding':'Official fee-calculator help lead only; help text does not establish a current payable amount.'},
 {'url':'https://www.bangladeshtradeportal.gov.bd/kcfinder/upload/files/Companies%20Act-1994.pdf','finding':'138-page official-hosted Bangla Act candidate. Extraction contains legacy-font corruption; not accepted as complete/current schedules package.'}
]
audit={'checked_at':now,'status':'blocked_live_access_local_preflight_completed','live_changes_applied':False,
 'blocker':'Browser runtime fails helper_sandbox_lock_failed / SetNamedSecurityInfoW error 5; direct project API probe returned HTTP 401. Authenticated live session unavailable.',
 'baseline_warning':'Existing per-source metadata and test results describe 13 September work; none were freshly confirmed live in this audit.',
 'diagnosis_file':str(out/'failed-query-diagnosis.json'),'supplied_response_generation':a['source_metadata_generation'],
 'baseline_log_generation':log.get('source_generation'),'source_rows':auditrows,'new_local_file':newfile,
 'metadata_preservation':'SourceRevisionCreate has no official_title or language fields. Preserve exact official title and language in change_reason and existing document metadata without changing detected language. Clone fresh live revision fields/edges, never write a title-only default Active revision.',
 'official_source_leads':webrefs,
 'tests':[{'id':name,'question':q,'status':'not_run_blocked_live_access','fresh_conversation_required':True} for name,q in probes],
 'local_code_fix':{'status':'implemented_not_deployed','description':'Use non-calculation authority refusal wording in English and Bangla for non-calculation questions; preserve calculation wording and authority enforcement.',
 'validation':'8 focused tests passed: four bilingual wording cases plus existing unresolved-authority enforcement/web suppression cases.'},
 'tax_preservation':{'live_mutations':0,'configuration_mutations':0,'fresh_regression_pass':False},
 'unresolved_gaps':['Complete Companies Act including schedules and current amendments','Current RJSC incorporation/annual filing forms, fee instruments and procedure captures','Company-specific income-tax registration, nil-return and applicable-year evidence','VAT/BIN registration and nil-return/deregistration conditions','Municipal/licensing source corrections and unresolved DBID/labour/fire/BSTI relationships','Fresh live metadata/OCR/index/citation checks and bilingual conversations','Live deployment of local wording fix, if desired']}
log['company_readiness_audit_2026_09_14']=audit
log['status']='company_readiness_blocked_live_access'
logpath.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8')
(out/'audit-preflight.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
report=root/'business-upload-report.md'; old=report.read_text(encoding='utf-8')
intro='''# Company-law readiness follow-up — 14 September 2026

**Status: incomplete; live access blocked.** No live sources, lifecycle states, relationships, project settings or tax settings were changed in this follow-up. Browser initialization failed with `helper_sandbox_lock_failed / SetNamedSecurityInfoW error 5`; the direct project API returned HTTP 401. The earlier report below is historical, not a fresh live certification.

**Failed API query:** the supplied response is a successful API envelope containing an `insufficient_evidence` answer, not an endpoint timeout. Message `48586933-78d6-41c8-a930-5d6d52406153`, conversation `c9038a44-4594-47c8-8aa3-1b273a23b514`, source generation 76, index build `c9c7a72c-1fb4-4c9f-93f1-6efba17da5e7`. Eleven passages passed relevance admission; zero survived final selection after authority/coverage recovery. Generation did not run. Twelve recovery searches failed to establish complete continuing company/RJSC, company tax, VAT/BIN and non-compliance rules. Total latency was 91.935 seconds. Six preparation LLM calls explain recorded token usage despite zero final-generation time.

Tax authority records identify unscoped relationships `967b419c-29e3-4241-8e1e-2c87b26a7aeb` (Finance Act 2026) and `dead04b4-aa50-4cb3-8b57-7dd4f30234d6` (Ordinance 52/2025), both targeting Income Tax Act English revision `d85d4223-3cdf-47c0-aff8-f73d1de5744f`. Relationship `2934fd38-b3ce-4235-8ec9-c3477570747c` (Ordinance 59/2025) was excluded for incomplete metadata. Two older revisions were correctly excluded as stale. These are recorded pre-existing tax issues; no tax metadata was edited. Web fallback was explicitly `suppressed_unresolved_authority`. Do not remove authority checks to manufacture a passing answer.

**Local fix, not deployed:** non-calculation questions now receive a bilingual authority-refusal explanation without an irrelevant “final calculation” claim. Calculation refusal wording, retrieval and enforcement are preserved. Eight focused tests passed, including existing authority enforcement and web-suppression tests. This wording correction does not repair corpus coverage or establish a successful live answer.

**New English Companies Act file:** 90 pages, ending at section 404; no appended schedules. Source ID: none (not uploaded in this follow-up). Keep as a Draft candidate until authenticity, amendment coverage and schedules are resolved. Printed title: “The Companies Act (Bangladesh), 1994”; printed SRO 177-law/1995 claim is not independently authenticated. PDF creation metadata is 2008, not a legal effective date. Its relationship to the Bangla Act is potentially parallel language, not amendment. First/last text and schedule headings were inspected; rendered-page viewing was also blocked, so visual QA is not claimed.

**Source-by-source preflight:** all 27 previously uploaded business-source records were reviewed from the existing log, including their recorded metadata, processing and relationships. No record was revalidated live. English display titles below are staged only. Preserve source language, exact official heading and all fresh live metadata/edges when applying a correction. The API schema has no separate official-title/language fields on source revisions: preserve those details in the change reason and existing document metadata. Do not default a Draft source to Active during a rename.

| Source ID | Proposed English display title | Previous lifecycle; current audit |
|---|---|---|
'''
for row in auditrows:
 if row['document_id']: intro+=f"| `{row['document_id']}` | {row['proposed_display_title']} | {row['baseline_lifecycle']}; live check pending |\n"
intro+='''
Draft reasons remain those documented per source below. No reliable primary source was newly activated because fresh completeness, authority and live-state verification was unavailable. Local SHA-256 comparisons, preserved original titles, source IDs, relationships and proposed title changes are recorded in the JSON follow-up. The old Bangla Companies Act local path is now missing; this is not evidence that its live source was removed.

**Official leads identified, not validated for activation:** [current Companies Act](https://bdlaws.minlaw.gov.bd/act-print-788.html), [RJSC forms including Schedule X](https://roc.gov.bd/pages/forms/6922d9b8933eb65569dffa1c), [RJSC fee-calculator help](https://app.roc.gov.bd/help/fee_calculator.htm), and [official-hosted 138-page Bangla Act candidate](https://www.bangladeshtradeportal.gov.bd/kcfinder/upload/files/Companies%20Act-1994.pdf). The form page is dated 2016; a recent crawl does not prove currency. The 138-page PDF has corrupted native extraction and is not certified as a complete schedules package. Obtain the actual schedules and current fee instruments, reconcile amendments and procedure dates, then validate OCR and citations before activation.

**Fresh tests:** six English/Bangla test prompts are staged in the JSON for incorporation, schedules, the exact non-operating-company query, and salary-tax regression. All are **not run — live access blocked**. Prior tax tests below were partial; no fresh tax pass is claimed. Recheck every changed source in fresh bilingual conversations, record citation/source/page anchors and job/build IDs, and compare tax source revision IDs and project configuration before/after.

Detailed diagnosis: [failed-query-diagnosis.json](E:/python-projects/rag-builder/artifacts/company-readiness/failed-query-diagnosis.json). Machine-readable follow-up: [audit-preflight.json](E:/python-projects/rag-builder/artifacts/company-readiness/audit-preflight.json).

---

'''
report.write_text(intro+old,encoding='utf-8')
print(json.dumps({'source_rows':len(auditrows),'uploaded_source_rows':sum(bool(x['document_id']) for x in auditrows),'staged_renames':len(titles),'local_hash_mismatches':[x['file'] for x in auditrows if x.get('local_sha256_matches_baseline') is False],'local_missing':[x['file'] for x in auditrows if not x['local_present']],'log_bytes':logpath.stat().st_size}))
