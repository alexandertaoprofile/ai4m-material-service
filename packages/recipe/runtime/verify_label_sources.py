"""Trace contradictory paired tensile labels back to admitted source tables.

This is an audit; do not silently swap labels, drop a test row, or change v10.
"""
from pathlib import Path
import json
import hashlib
import pandas as pd
ROOT=Path(__file__).resolve().parent
specs=[('al-1067',637.,581.,'yield_strength_MPa','uts_MPa'),
       ('ti-97',1710.,1665.,'yield_strength_tensile_mpa','ultimate_tensile_strength_mpa'),
       ('ss-383',148.,47.,'yield_0p2_MPa','uts_MPa')]
rows=json.loads((ROOT/'PHYSICAL_LABEL_REVIEW_ROWS.json').read_text())
out=[]
for record,ys,uts,yk,uk in specs:
    metadata=next(r for r in rows if r['record_id']==record+':YS')
    path=Path(metadata['source']);df=pd.read_csv(path,low_memory=False)
    match=df.loc[pd.to_numeric(df[yk],errors='coerce').eq(ys)&
                 pd.to_numeric(df[uk],errors='coerce').eq(uts)]
    # Include all raw metadata for a material scientist to resolve the record.
    # Source indices are zero-based; CSV row numbers include the header.
    parsed=json.loads(match.to_json(orient='records',force_ascii=False))
    source_rows=[{'zero_based_index':int(i),'csv_line':int(i)+2,'fields':r}
                 for i,r in zip(match.index,parsed)]
    out.append(dict(record_id=record,source=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    frozen_role=metadata['role'],source_matches=len(match),source_rows=source_rows,
                    verdict='contradiction_already_present_in_admitted_source_table' if len(match) else 'source_mapping_requires_review',
                    automatic_correction_performed=False,test_score_recomputed_after_exclusion=False,
                    required_action='check original experiment/table; issue a new audited dataset/model only if the correction is substantiated'))
(ROOT/'LABEL_SOURCE_VERIFICATION.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
print(json.dumps([{k:v for k,v in r.items() if k!='source_rows'} for r in out],ensure_ascii=False))
