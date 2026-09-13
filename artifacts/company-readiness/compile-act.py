from pathlib import Path
from lxml import html
import pymupdf,json,re
out=Path('artifacts/company-readiness')
r=html.fromstring((out/'act.html').read_text(encoding='utf-8')); c=r.get_element_by_id('hide')
sections=[c[0],c[2],c[3]]
for section in sections:
 for el in section.iter():
  for attr in ['style','width','height','onclick']:el.attrib.pop(attr,None)
 for el in section.xpath('.//script|.//style|.//button'):el.drop_tree()
body=''.join(html.tostring(s,encoding='unicode') for s in sections)
body=re.sub(r'\s+',' ',body)
# The source text and amendment footnotes remain verbatim; only presentation changes.
preface='''<h1>Companies Act, 1994</h1><h2>Bangladesh - official-source reading compilation</h2><p>Prepared 14 September 2026 from official components. This compilation is not a newly issued official consolidation or legal instrument.</p><p>Original title: কোম্পানী আইন, ১৯৯৪. Source language: Bangla. Part A reproduces the current BDLaws sections and all 33 amendment notes, including changes incorporated through 2020. Original Act publication: 12 September 1994. Original commencement: 1 December 1994 per BDLaws note. Capture date does not establish historical wording.</p><p>Part B reproduces BDLaws Schedule I, substitutes the complete Schedule II issued by SRO 101-Law/2019 (Gazette 23 April 2019, immediate commencement), and reproduces Schedules III-XII. The older Schedule II fee text on source PDF pages 25-33 is excluded from the operative compilation; the opening of Schedule III on page 33 is retained. No other substantive source text is intentionally edited.</p><p>Read the current Act with its schedules. Schedule I is a model set of articles, subject to the Act and the company's articles; it does not make every rule universal. The Schedule II Gazette does not establish stamp duty, every third-party charge, or later fee changes. Current RJSC procedure and fee checks remain separate.</p><p>Sources:<br>https://bdlaws.minlaw.gov.bd/act-print-788.html<br>https://bdlaws.minlaw.gov.bd/upload/act/2020-12-30-16-34-32-788___Schedule.pdf<br>https://roc.gov.bd/pages/files/6922da04933eb65569e01dcf</p><p>PDF citations use compilation pages. Printed original page numbers remain visible on appended official schedule/Gazette pages. Retain the original downloaded files for provenance.</p><h2>Part A - Consolidated Act and amendment notes</h2>'''
css='''@font-face {font-family: Bengali;src:url(nirmala.ttc);} body{font-family:Bengali,sans-serif;font-size:10pt;line-height:1.45;}h1{font-size:20pt;}h2,h3{font-size:15pt;}h4{font-size:12pt;} .txt-head,.act-part-group,.act-chapter-group{font-weight:bold;margin-top:10pt;}p{margin:5pt 0;}table{border-collapse:collapse;width:100%;}td,th{border:0.4pt solid #bbb;padding:3pt;} .footnoteListAll{font-size:9pt;} a{color:#111;text-decoration:none;}'''
story=pymupdf.Story(html=preface+body,user_css=css,archive=pymupdf.Archive('C:/Windows/Fonts'))
path=out/'Companies Act 1994 - Current Act and Schedules.pdf'
w=pymupdf.DocumentWriter(str(out/'act-body-render.pdf')); page=pymupdf.paper_rect('a4'); where=page+(40,40,-40,-40)
count=0
while True:
 dev=w.begin_page(page);more,_=story.place(where);story.draw(dev);w.end_page();count+=1
 if not more:break
 if count>400:raise RuntimeError('Unexpected excessive pagination')
w.close()
b=pymupdf.open(out/'act-body-render.pdf'); schedules=pymupdf.open(out/'Companies Act 1994 - Official Schedules.pdf');fees=pymupdf.open(out/'RJSC Fee Gazettes.pdf')
b.insert_pdf(schedules,from_page=0,to_page=23)
b.insert_pdf(fees)
rects=schedules[32].search_for('তফিসল-৩');assert len(rects)==1,rects
clip=pymupdf.Rect(0,rects[0].y0-6,schedules[32].rect.width,schedules[32].rect.height)
# Cropping retains the beginning of Schedule III while excluding the older fee provisions.
p=b.new_page(width=schedules[32].rect.width,height=clip.height);p.show_pdf_page(p.rect,schedules,32,clip=clip)
b.insert_pdf(schedules,from_page=33)
b.set_metadata({'title':'Companies Act 1994 - Current Act and Schedules','subject':'Official-source reading compilation; source provenance on opening pages','author':'Compiled from BDLaws and RJSC official components'})
b.save(path,garbage=4,deflate=True)
manifest={'output':str(path.resolve()),'body_pages':count,'total_pages':len(b),'schedule_II_original_pages_excluded':[25,33],'schedule_III_crop_y':rects[0].y0,'fee_gazette_pages':len(fees),'source_components':['act.html','Companies Act 1994 - Official Schedules.pdf','RJSC Fee Gazettes.pdf'],'visual_qa':'pending'}
(out/'compilation-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
for i in [0,2,count-1,count,count+24,count+28,len(b)-1]:b[i].get_pixmap(matrix=pymupdf.Matrix(1.1,1.1)).save(str(out/f'compilation-qa-{i+1}.png'))
print(json.dumps(manifest))
