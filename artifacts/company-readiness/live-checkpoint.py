import json
from pathlib import Path
from datetime import datetime, timezone

root = Path(__file__).resolve().parents[2]
p = root / 'business-upload-log.json'
log = json.loads(p.read_text(encoding='utf-8'))
a = log['company_readiness_audit_2026_09_14']
a.setdefault('historical_blocked_preflight', {k: a[k] for k in ['checked_at','status','blocker','baseline_warning']})
a.update(checked_at=datetime.now(timezone.utc).isoformat(), status='live_audit_in_progress_act_ocr_pending', live_changes_applied=True, blocker=None, baseline_warning='Live source metadata and processing inspected for all 35 baseline sources; 27 business documents. Fresh answer validation remains in progress.')
renames = [
('eca42359','Consumers Right Protection Act, 2009',3),
('f5cbfdfb','DNCC — Trade Licence Issuance and Renewal Procedure (17 July 2025)',2),
('bcb74cd8','DSCC — Trade Licence Issuance and Renewal Procedure (28 January 2025)',2),
('241c687a','RJSC — Entity Registration Procedure (Undated Capture)',2),
('9e2ca5ca','Bangladesh Labour Act, 2006 — Consolidated Through 2026 (Schedules Missing)',2),
('b6f93c98','Bangladesh Labour (Amendment) Act, 2026 — Act 43 of 2026',2),
('c724d392','Bangladesh Labour Rules, 2015 — Base Rules, SRO 291-Law/2015',2),
('d5361d1a','Bangladesh Labour Rules, 2015 — Amendment SRO 284-Law/2022',2),
('483bdc35','Bangladesh Labour Rules, 2015 — Amendment SRO 53-Law/2026',2),
('8bbc2453','Digital Commerce Operation Guidelines, 2021',3),
('c791a8cc','Digital Business Identity (DBID) Registration Guidelines, 2022 — Signed Package',2),
('201bdcc8','DBID Registration Guidelines 2022 — Paragraph 4.1.2 Amendment Circular (18 June 2023)',2),
('2bb90c33','Bangladesh Environment Conservation Act, 1995 — Consolidated Text',3),
('6aee09d1','Environment Conservation Rules, 2023 — SRO 53-Law/2023',3),
('abcc16c0','Fire Prevention and Extinction Rules, 2014 — SRO 230-Law/2014',2),
('39ac4bf0','Importers, Exporters and Indentors (Registration) Order, 2023 — SRO 326-Law/2023',2),
('af30a67a','Trademarks Act, 2009 — Consolidated Including 2015 and 2023 Changes',3)]
for row in a['source_rows']:
    if not row.get('document_id'):
        continue
    absent = row['document_id'].startswith('30e9188a')
    row.update(live_metadata_checked=not absent, live_processing_checked=not absent, decision='absent_from_live_inventory_not_deleted_by_audit' if absent else 'prior_lifecycle_preserved_after_live_review')
    for i,(prefix,title,rev) in enumerate(renames):
        if row['document_id'].startswith(prefix):
            row.update(title_change_applied=True, live_display_title=title, live_revision_number=rev, verified_source_generation=78+i)
            for f in log['files']:
                if f.get('ids',{}).get('document_id')==row['document_id']:
                    f['metadata'].update(source_title=title, original_title_preserved_in_change_reason=row['original_title_to_preserve'], source_language='Bangla')
                    f['ids']['previous_revision_id']=f['ids'].get('revision_id')
                    f['ids']['revision_id']=None
                    f['ids']['revision_number']=rev
                    f['live_review_2026_09_14']='Metadata and ready processing verified; previous lifecycle, dates and relationships preserved.'
a['live_changes']={'title_only_corrections':17,'source_generations':[77,94],'english_act':{'document_id':'50415d8d-3102-4ae6-9d7b-83ae10089cbf','revision_number':2,'revision_id_prefix':'2c8144bb','lifecycle':'draft','source_title':'Companies Act, 1994 — English Text (Schedules Missing; Amendment Currency Unverified)','published_at':None,'work_identity':'bd.business.companies-act-1994','reason':'Incorrect 1932-02-10 date cleared. 90 pages end at section 404; schedules absent, amendment currency and translation authenticity unresolved.'}}
a['new_complete_act']={'document_id':'5e65a99d-09e5-494d-ab44-5de8d00d4042','process_job_id':'6cab5dae-7f02-40cd-95b4-96527f12edd1','lifecycle':'draft','revision_number':1,'pages':256,'ocr_override':'bn','status':'process_running_10_percent_last_observed','work_identity':'bd.business.companies-act-1994','published_at':'1994-09-12','effective_from':'1994-12-01','dates_scope':'Original Act only; not all consolidated provisions historical applicability','relationships':[],'source_role':'primary','compilation':'Unofficial reading compilation preserving official BDLaws body and Schedules I-XII, with original Schedule II excluded and SRO101-Law/2019 substituted. Already-incorporated amendments not applied twice.','manifest':'artifacts/company-readiness/compilation-manifest.json'}
a['tax_preservation_live']={'policy_revision':10,'policy_id':'0c58c848-3c5e-4796-a5cc-736a0aaf7d6c','effective_hash':'94941a1a3bd5','resolution_hash':'af420b288290','tax_revisions_unchanged':['5d517f44','d85d4223','6f9080ae','4cdc790a','4dd08530','3fcef8b4','ac595c5a','ecf9fe2d'],'source_or_settings_mutations':0,'fresh_answer_tests':'in_progress'}
a['official_live_checks']={'registration_page':{'url':'https://roc.gov.bd/pages/static-pages/6922dd32933eb65569e13e40','page_updated':'2025-01-26','result':'Only heading loaded; substantive procedure unavailable in current rendered page'},'forms_catalogue':{'url':'https://roc.gov.bd/pages/forms/6922d9b8933eb65569dffa1c','page_updated':'2016-08-08','result':'28 official PDF forms linked; individual current applicability pending'},'fee_calculator':{'url':'https://app.roc.gov.bd/psp/fee_calculator','example':{'business_type':'Registration','entity_type':'Private Company','authorized_capital':1000000,'registration_fee':0,'filing_fee':1200,'MOA_AOA_stamp':12300,'certified_copy_MOA_XII_digital_certificate':1520,'displayed_note':'15% VAT will be added','scope':'Specific illustrative current calculator result; do not infer VAT base or universal total'}}}
log['status']=a['status']
p.write_text(json.dumps(log,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
mp=root/'artifacts/company-readiness/compilation-manifest.json'
m=json.loads(mp.read_text(encoding='utf-8'));m['visual_qa']={'passed_pages':[1,3,132,133,157,161,256],'cropped_schedule_III_native_extraction':'Verified no excluded Schedule II text','scope':'Selected-page visual QA; live OCR/citation verification pending'};mp.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
rp=root/'business-upload-report.md'
old=rp.read_text(encoding='utf-8')
marker='## Historical preflight report'
if marker not in old:
    old=marker+'\n\n'+old
else:
    old=old[old.index(marker):]
intro='''# Company-law readiness live audit — 14 September 2026

**In progress: live access restored; Act OCR and fresh conversations pending.** All 35 baseline sources were inspected live, including metadata, relationships and the processing facts for all 27 business documents. No tax sources or proven settings were changed.

Seventeen Bangla display titles were changed to English (generations 78–94), preserving original Bangla titles in revision reasons and existing language, lifecycle, dates and relationships. The English Companies Act source `50415d8d-3102-4ae6-9d7b-83ae10089cbf` was corrected at generation 77: the erroneous 1932 publication date was cleared and the incomplete 90-page translation was made Draft, revision 2. The old Bangla Act source `30e9188a-53d9-4810-9985-30628d5d4687` was absent from the live inventory; this audit did not delete it.

A 256-page reading compilation of the current official BDLaws Act body and all schedules was uploaded as Draft source `5e65a99d-09e5-494d-ab44-5de8d00d4042`. Original Schedule II was excluded and replaced by RJSC’s SRO 101-Law/2019. This is explicitly an unofficial assembly of official components. Selected boundary and final pages passed visual checks; processing job `6cab5dae-7f02-40cd-95b4-96527f12edd1` is running with explicit Bangla OCR. Activation requires successful OCR/index/citation validation. Components and manifest are in `artifacts/company-readiness`.

RJSC’s current registration page rendered only its heading. The official forms catalogue provides 28 PDF links but individual applicability remains to be validated. The live fee calculator worked: private company, authorised capital Tk 1,000,000 → registration 0, filing 1,200, MOA/AOA stamps 12,300, certified copy/certificate 1,520, plus a displayed 15% VAT note. This is one scenario, not a universal price or verified VAT base.

The supplied API response failed final authority/coverage selection, not transport: 11 relevant passages, zero final evidence, `unresolved_authority`, 12 recovery searches and no answer generation. Missing company/RJSC and VAT coverage and pre-existing unresolved tax relationships remain material. A local bilingual refusal-wording fix passed eight focused tests; it is not deployed and does not itself fix missing evidence.

Tax policy revision 10 (`0c58c848-3c5e-4796-a5cc-736a0aaf7d6c`) and effective hash `94941a1a3bd5` were preserved. Fresh tax and company answers are not yet certified. All source IDs, decisions and verified changes are recorded in `business-upload-log.json`. Earlier blocked findings below are historical and superseded where this live checkpoint differs.

'''
rp.write_text(intro+old,encoding='utf-8')
print('Live checkpoint saved; validation remains in progress')
