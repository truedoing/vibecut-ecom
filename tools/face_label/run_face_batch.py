# -*- coding: utf-8 -*-
import csv, sys
import face_lib as F
from pathlib import Path
lo=int(sys.argv[1]); hi=int(sys.argv[2])
centers,_=F.build_centers()
rows=list(csv.DictReader(open('真值样本.csv',encoding='utf-8-sig')))
for r in rows:
    i=int(r['id'])
    if not (lo<=i<hi): continue
    p=next(Path('truth_frames').glob(f'{i:03d}_*.jpg'),None)
    if p:
        b,d,v=F.recognize(p,centers)
        r['who_人脸']=b or ''; r['face_dist']=f'{d:.3f}' if d is not None else ''; r['face_verdict']=v
    else:
        r['who_人脸']=''; r['face_dist']=''; r['face_verdict']='no_frame'
with open('真值样本.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print('done',lo,hi)
