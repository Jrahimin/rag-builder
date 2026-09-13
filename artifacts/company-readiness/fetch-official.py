import requests,json
from lxml import html
from pathlib import Path
urls={
'act':'https://bdlaws.minlaw.gov.bd/act-print-788.html',
'rjsc_laws':'https://roc.gov.bd/site/page/8bac7fef-e446-4c40-84a6-9f3419b733c5/',
'rjsc_registration':'https://roc.gov.bd/site/page/855dc577-3035-4ca4-b376-49c517099a3e/',
'rjsc_forms':'https://roc.gov.bd/pages/forms/6922d9b8933eb65569dffa1c'}
out=Path('artifacts/company-readiness')
for name,url in urls.items():
 try:
  r=requests.get(url,timeout=25);r.raise_for_status(); (out/f'{name}.html').write_bytes(r.content); t=html.fromstring(r.content)
  links=[{'text':a.text_content().strip()[:180],'url':a.get('href')} for a in t.xpath('//a[@href]') if any(x in (a.get('href') or '').lower() for x in ['pdf','doc','static','files'])]
  print(json.dumps({'name':name,'status':r.status_code,'bytes':len(r.content),'links':links[:80],'head':t.text_content().strip()[:220]},ensure_ascii=False))
 except Exception as e: print(name,type(e).__name__,str(e)[:100])
