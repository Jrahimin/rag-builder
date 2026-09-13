from pathlib import Path
from datetime import datetime,timezone
import json,hashlib
root=Path(__file__).resolve().parents[2]; out=root/'artifacts/company-readiness'
p=root/'business-upload-log.json';d=json.loads(p.read_text(encoding='utf-8'));a=d['company_readiness_audit_2026_09_14']
a['checked_at']=datetime.now(timezone.utc).isoformat()
a['status']='live_validation_blocked_backend_unavailable'
a['live_blocker']='Both Test Lab tabs reported Backend unavailable after original English/Bangla dormant-company query submissions. Try again also showed Backend unavailable. No successful result observed; do not blindly resubmit duplicate queries.'
a['full_company_readiness_certified']=False
a['source_metadata_generation']=101
a['tax_preservation_live']['final_policy_rechecked']=True
a['tax_preservation_live']['fresh_answer_tests']='English registration grounded; Bangla registration and bilingual calculations partial. Full tax regression not passed.'
a['live_counts']={'total_project_documents':38,'existing_tax_sources':8,'business_sources':30,'active_business_sources':11,'draft_business_sources':19,'active_all_sources':19,'draft_all_sources':19,'ready_documents':38}
a['new_complete_act'].update(lifecycle='draft',revision_number=3,status='ready')
for component in a['structured_components']:
    component.update(lifecycle='active',revision_number=2,status='ready',work_identity='bd.business.companies-act-1994',source_role='primary',metadata_verified=True)
    if component['component'].startswith('B'):
        component.update(embed_job_id='9a8fbc72-1ad9-4cbd-8a83-6fa5b4f47967',parser='ocr',published_at=None,effective_from=None)
    else:component.update(published_at='1994-09-12',effective_from='1994-12-01')
newtests=[
dict(id='original_company_en_retest',result='submitted_result_unavailable_backend_outage',limitation='Fresh conversation submitted; new conversation ID not captured before backend unavailable screen. No result certified.'),
dict(id='original_company_bn_retest',result='submitted_result_unavailable_backend_outage',limitation='Fresh conversation submitted; new conversation ID not captured before backend unavailable screen. No result certified.'),
dict(id='tax_en_calculation',conversation_id='fc016038-c267-4e18-a8e3-a42c932b8b60',result='partial',claims='11/22',latency_ms=55880,limitation='Investment rebate not established; final tax not computed'),
dict(id='tax_bn_calculation',conversation_id='164d3366-4c6a-4864-a8c6-54f25a2f0d7d',result='partial',claims='11/27',latency_ms=52312,limitation='Slabs and minimum tax not established; final tax not computed'),
dict(id='structured_company_bn_same_query',conversation_id='a09dfe40-fc5b-4b27-aed6-4fdbdd2cefe1',result='substantive_deadlines_improved_citation_format_failed',claims='0/6',latency_ms=11938,limitation='Correct18/15month and21day deadlines; page-qualified citations not parsed, and ScheduleX page attribution unreliable'),
dict(id='structured_company_en_same_query',conversation_id='3b01e34b-906d-4a3a-acbd-9720577ba873',result='insufficient_evidence',latency_ms=55504,reason='unresolved_authority'),
dict(id='structured_company_bn_citation_format',conversation_id='37507c41-8f39-474e-833f-880fcb466866',result='partial',claims='2/3',latency_ms=12093,limitation='Test erroneously asked for deadline in36(4); response did not correct subsection. Deadline is36(3).'),
dict(id='structured_company_en_citation_format',conversation_id='e70bb5b7-0412-4d21-8109-b4df9cb058a8',result='partial',claims='2/3',latency_ms=8096,limitation='Corrected erroneous test premise36(4) to36(3); backend did not fully verify answer')]
existing={t['id'] for t in a['fresh_live_tests']};a['fresh_live_tests'] += [t for t in newtests if t['id'] not in existing]
a['citation_diagnosis']={'observed':'Generated [1, পৃষ্ঠা ১] and compound page-qualified brackets were not recognised as [1] citation tokens. Grounding service regex only accepts bracketed digits.','code_location':'backend/app/modules/conversations/grounding_service.py:41','fix_status':'Not changed or deployed; preserve actual claim verification rather than treating citations as automatic support.'}
a['validation_scope']='All live source metadata/lifecycles and original business processing facts reviewed; changed titles rechecked in final inventory. Fresh bilingual tests cover company-law and tax scenarios, not each of the17 title-only changes individually. Full requested per-source bilingual answer suite remains incomplete.'
a['unresolved_gaps']=[
'Broad dormant-company and incorporation readiness is not certified: applicability/authority recovery still withholds some answers.',
'Current RJSC registration page contains no rendered substantive procedure; old local capture remains Draft. Complete current procedures, all forms and operational fee rules are not fully ingested.',
'Seven official forms downloaded locally, not uploaded; legacy fee labels and form extraction/current-applicability issues prevent blanket activation.',
'Native statutory/schedule separation improves retrieval but does not yet yield fully verified bilingual company-law answers; citation formatting and source/page attribution require further remediation.',
'Company nil-return, VAT/BIN, activity-specific licence applicability and consequences remain insufficient for comprehensive dormant-company advice.',
'Existing tax amendment scopes/effective metadata remain unresolved. User-protected tax sources/settings untouched; fresh tax calculations returned partial results.',
'Existing Draft labour, DBID, fire, BSTI and registration-order sources retain documented date/scope/parent-law/currency issues; do not infer broad licence requirements.',
'Local bilingual refusal wording fix passed8focused tests but is not deployed.',
'Fresh conversations for every title-only metadata change individually have not been completed.']
d.setdefault('counts_before_live_audit',d['counts'])
d['counts']={**a['live_counts'],'original_local_files':29}
d['source_generation']=101;d['status']=a['status']
for f in d['files']:
    sourceid=f.get('ids',{}).get('document_id')
    if sourceid=='30e9188a-53d9-4810-9985-30628d5d4687':f.update(live_inventory_status='absent_not_deleted_by_this_audit')
    if sourceid=='e01531e9-a216-4c18-87bf-7517d92fb949':
        f['metadata'].update(lifecycle='active',change_reason_summary='Live r4 retained: full section25 is present in page8 chunk57. Earlier truncation was a preview issue.')
        f['ids'].update(revision_number=4)
        f['live_review_2026_09_14']='Local text and full live retrieval agree; chunk969add3c chars11752–11903.'
newfiles=[('Company Act, 1994 (english).pdf','50415d8d-3102-4ae6-9d7b-83ae10089cbf','draft',2,'Companies Act, 1994 — English Text (Schedules Missing; Amendment Currency Unverified)',Path(r'C:\Users\user\Downloads\BD Business Docs\Company Act, 1994 (english).pdf')),
('Companies Act 1994 - Current Act and Schedules.pdf','5e65a99d-09e5-494d-ab44-5de8d00d4042','draft',3,'Companies Act, 1994 — Consolidated Body and Schedules I–XII (Official Components)',out/'Companies Act 1994 - Current Act and Schedules.pdf')]
for comp in a['structured_components']:
    title='Companies Act, 1994 — Part A: Complete Statutory Sections (Official Bangla)' if comp['component'].startswith('A') else 'Companies Act, 1994 — Part B: Schedules I–XII and Model Articles (Official Components)'
    newfiles.append((comp['file'],comp['document_id'],'active',2,title,out/comp['file']))
for name,sid,lifecycle,revision,title,path in newfiles:
    existingfile=next((f for f in d['files'] if f.get('ids',{}).get('document_id')==sid),None)
    if existingfile:continue
    f={'file':name,'path':str(path),'ids':{'document_id':sid,'revision_number':revision},'metadata':{'source_title':title,'source_role':'primary','lifecycle':lifecycle,'work_identity':'bd.business.companies-act-1994','source_language':'English' if sid.startswith('50415') else 'Bangla'},'status':'ready','relationships':[],'audit_details':'See company_readiness_audit_2026_09_14 for dates, provenance, processing, complementary-part relationships and validation.'}
    if path.exists():f.update(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    d['files'].append(f)
p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
rows=[]
for f in d['files']:
    sid=f.get('ids',{}).get('document_id')
    if not sid:continue
    meta=f.get('metadata',{})
    rows.append('| '+ ' | '.join([sid,meta.get('source_title',f['file']),f.get('live_inventory_status',meta.get('lifecycle','unknown')),meta.get('change_reason_summary','See detailed audit record')])+' |')
testrows=['| '+ ' | '.join([t['id'],t.get('conversation_id',''),t['result'],t.get('claims','—')])+' |' for t in a['fresh_live_tests']]
report=f'''# Bangladesh company-law readiness audit — 14 September 2026

**Readiness is not certified; further live validation is blocked.** Live access was restored and the corpus was improved, but fresh company-law and tax tests still expose material gaps. Both original dormant-company retests ended at a Backend unavailable screen; a retry did not restore access. No project was created, no source was deleted, and no tax source or proven setting was changed.

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

The local bilingual refusal-wording correction in `backend/app/modules/conversations/services/chat_service.py` passed8focused tests and Ruff checks. It is **not deployed**, and does not repair missing authority or guarantee legal correctness.

## Fresh conversation tests

| Test | Conversation ID | Result | Supported claims |
|---|---|---|---|
{chr(10).join(testrows)}

Company statutory retrieval was checked at Act section81, section36, ScheduleX, ScheduleXII and replacement ScheduleII. After native restructuring, the same Bangla query retrieved section81 chunk97 (`f5c92c5f`) and section36 chunk49 (`9c5daade`). An additional test deliberately/incorrectly referenced36(4); English corrected it to36(3), whereas Bangla did not. This is recorded as a premise-handling limitation, not a passing legal test. Tax registration in English passed, but bilingual tax calculations remained partial. **A full tax regression pass is not claimed.**

Fresh bilingual conversations were not completed individually for every title-only change. Metadata/lifecycle persistence was checked for those changes; the requested full per-source answer suite remains incomplete.

## Source decisions

The detailed JSON retains dates, hashes, IDs, processing choices, previous revisions and relationships. The following table includes the historical missing source explicitly; it is not counted in the live38.

| Source ID | Display title | Lifecycle / inventory | Decision reason |
|---|---|---|---|
{chr(10).join(rows)}

## Unresolved work

{chr(10).join('- '+g for g in a['unresolved_gaps'])}
'''
(root/'business-upload-report.md').write_text(report,encoding='utf-8')
print('Saved audit report with explicit readiness gaps and current source decisions')
