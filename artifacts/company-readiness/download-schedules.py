import requests,re,json,hashlib
from pathlib import Path
import pymupdf
out=Path('artifacts/company-readiness');out.mkdir(exist_ok=True)
u='https://bdlaws.minlaw.gov.bd/upload/act/2020-12-30-16-34-32-788___Schedule.pdf'
r=requests.get(u,timeout=35);r.raise_for_status();p=out/'Companies Act 1994 - Official Schedules.pdf';p.write_bytes(r.content)
d=pymupdf.open(p)
print(json.dumps({'pages':len(d),'bytes':len(r.content),'sha256':hashlib.sha256(r.content).hexdigest(),'head':d[0].get_text()[:900],'tail':d[-1].get_text()[-700:]},ensure_ascii=False))
for i in [0,len(d)-1]: d[i].get_pixmap(matrix=pymupdf.Matrix(1.2,1.2)).save(str(out/f'schedules-page-{i+1}.png'))
