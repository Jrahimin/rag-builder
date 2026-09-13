import requests,json,hashlib
from pathlib import Path
import pymupdf
base='https://objectstorage.ap-dcc-gazipur-1.oraclecloud15.com/n/axvjbnqprylg/b/V2Ministry/o/office-roc/2024/12/'
files={'RJSC Companies Act 1994':'10dcb963168e4b2dbcaad72cc97d6c92.pdf','RJSC Schedule II fees 2018':'4bbaa5e3206943d79fdbfb0e1c524398.pdf','RJSC Fee Gazettes':'17c613279c354d88a7030fd1bada070d.pdf','Companies Second Amendment Act 2020':'38c9987e079f4a7e8f31b389ea9aafe9.pdf'}
for name,f in files.items():
 try:
  r=requests.get(base+f,timeout=30);r.raise_for_status();p=Path('artifacts/company-readiness')/(name+'.pdf');p.write_bytes(r.content);d=pymupdf.open(p)
  print(json.dumps({'file':str(p),'pages':len(d),'bytes':len(r.content),'hash':hashlib.sha256(r.content).hexdigest(),'first':d[0].get_text()[:650],'last':d[-1].get_text()[-300:]},ensure_ascii=False))
  for i in [0,len(d)-1]:d[i].get_pixmap(matrix=pymupdf.Matrix(1.3,1.3)).save(str(p.with_suffix(''))+f'-page-{i+1}.png')
 except Exception as e: print(name,type(e).__name__,str(e)[:150])
