from pathlib import Path
from lxml import html
import re,json,fitz
out=Path(__file__).resolve().parent
r=html.fromstring((out/'act.html').read_text(encoding='utf-8')); c=r.get_element_by_id('hide')
norm=lambda s:re.sub(r'\s+',' ',s).strip()
parts=['# Companies Act, 1994 — STATUTORY SECTIONS (Bangla official text)\n\nOriginal title: কোম্পানী আইন, ১৯৯৪. Official source: https://bdlaws.minlaw.gov.bd/act-print-788.html . Captured 2026-09-14. Native text; no translation of operative words. This is Part A of a two-component complete reading bundle; Part B contains Schedules I–XII, including the substituted Schedule II. Part A is statutory sections, not the model articles contained in schedules. Original Act publication 1994-09-12; commencement 1994-12-01. Consolidated amendments are not backdated by these original dates.','## Official introduction\n\n'+norm(c[2].text_content())]
headings=[]
for row in c[3].xpath('./div[contains(@class,"lineremoves")]'):
    for heading in row.xpath('./div[contains(@class,"act-part-group") or contains(@class,"act-chapter-group")]'):
        parts.append('## '+norm(heading.text_content()))
    heads=row.xpath('./div[contains(@class,"txt-head")]'); bodies=row.xpath('./div[contains(@class,"txt-details")]')
    if not bodies:continue
    body=norm(bodies[0].text_content()); number=re.match(r'^([০-৯]+[ক-হ]?)',body)
    title=norm(heads[0].text_content()) if heads else ''
    label=number.group(1) if number else ''
    latin=label.translate(str.maketrans('০১২৩৪৫৬৭৮৯','0123456789'))
    parts.append(f'### Statutory section {latin} — ধারা {label} — {title}\n\n'+body)
    headings.append({'section':label,'title':title,'chars':len(body)})
notes=c[3].xpath('./div[contains(@class,"footnoteListAll")]')
parts.append('## Official amendment and commencement notes\n\n'+norm(notes[0].text_content()))
assert len(headings)==418,len(headings)
assert any(x['section']=='৮১' for x in headings)
path=out/'Companies Act 1994 - STATUTORY SECTIONS - Official Bangla.md'
path.write_text('\n\n'.join(parts)+'\n',encoding='utf-8')
src=fitz.open(out/'Companies Act 1994 - Current Act and Schedules.pdf');d=fitz.open()
cover=d.new_page();cover.insert_textbox(fitz.Rect(42,42,550,780),'''Companies Act, 1994 - SCHEDULES I-XII
Part B of the complete official-source reading bundle

Companion Part A: Companies Act 1994 - STATUTORY SECTIONS - Official Bangla.md

This component contains statutory schedules, prescribed forms, model memoranda
and model articles. Numbered provisions inside model articles are not statutory
sections with the same number. Apply the statutory Act and the relevant type of
company; do not use a model-article deadline as the text of statutory section 81.

Original Bangla pages follow without translation or rewording. The original
Schedule II is excluded and replaced by SRO 101-Law/2019, effective 23 April 2019.
That Gazette does not supply all current RJSC stamp, certificate or VAT charges.
Current operational procedures and charge types require their own official sources.

Sources:
https://bdlaws.minlaw.gov.bd/act-788.html
https://bdlaws.minlaw.gov.bd/upload/act/2020-12-30-16-34-32-788___Schedule.pdf
https://roc.gov.bd/pages/files/6922da04933eb65569e01dcf

Prepared from official components on 14 September 2026.
This reading assembly is not a newly issued official legal instrument.
Printed original page numbers remain visible; citations use component PDF pages.
''',fontsize=11,fontname='helv')
d.insert_pdf(src,from_page=132)
d.save(out/'Companies Act 1994 - SCHEDULES and MODEL ARTICLES I-XII.pdf',garbage=4,deflate=True)
d[0].get_pixmap().save(out/'components-cover-qa.png')
(out/'components-manifest.json').write_text(json.dumps({'native_statutory_sections':headings,'section_count':len(headings),'schedules_pages':len(d),'full_bundle':True,'component_relationship':'complementary parts of same work, not an amendment or replacement of each other'},ensure_ascii=False,indent=2),encoding='utf-8')
print({'sections':len(headings),'markdown_bytes':path.stat().st_size,'schedules_pages':len(d),'section81':[x for x in headings if x['section']=='৮১']})
