from lxml import html
from pathlib import Path
r=html.fromstring(Path('artifacts/company-readiness/act.html').read_text(encoding='utf-8'))
c=r.get_element_by_id('hide')
rows=c[3].xpath('.//div[contains(@class,"lineremoves")]')
print('rows',len(rows))
for row in rows[:2]:
    print(html.tostring(row,encoding='unicode')[:2000])
print('children',[(e.tag,e.get('class'),len(e.text_content())) for e in c[3]])
