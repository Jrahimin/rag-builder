from pathlib import Path
import requests, fitz, json, hashlib
out=Path(__file__).resolve().parent
base='https://objectstorage.ap-dcc-gazipur-1.oraclecloud15.com/n/axvjbnqprylg/b/V2Ministry/o/office-roc/2024/12/'
forms=[('I','7010afce4927436495e26e4e85703678'),('VI','d6b9b93094304651982c8643d1c7ea0d'),('IX','7092c91b1e9f42b9822c895efef3a2bf'),('X','1cdaa83f63f24cca9e8998dfcf42706d'),('XII','181d6398350844939bca1567b82d8adf'),('23B','23401717be59449a986d97b90bfc65f5'),('Schedule-X','7143720aedb14167a25caea3881b84dd')]
manifest=[]
for name,key in forms:
    url=base+key+'.pdf';p=out/('RJSC Form '+name+'.pdf')
    r=requests.get(url,timeout=45);r.raise_for_status();p.write_bytes(r.content)
    d=fitz.open(p);text='\n'.join(pg.get_text() for pg in d)
    manifest.append(dict(form=name,url=url,file=p.name,pages=len(d),sha256=hashlib.sha256(r.content).hexdigest(),text_sample=text[:1700],last_text=text[-200:]))
(out/'forms-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False,indent=2))
