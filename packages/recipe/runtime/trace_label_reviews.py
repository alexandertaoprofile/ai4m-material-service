from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parent
frame=pd.read_csv(ROOT/'frozen_data/alloy_v8.csv',low_memory=False)
stems={'al-1067','ss-383','ti-97'}
out=frame.loc[frame.record_id.str.replace(r':(?:YS|UTS)$','',regex=True).isin(stems)]
cols=[c for c in frame if not c.startswith(('desc_','canon_'))]
out[cols].to_json(ROOT/'PHYSICAL_LABEL_REVIEW_ROWS.json',orient='records',force_ascii=False,indent=2)
# Quarantine is a REVIEW delivery, not silent removal from published scores
# and not a modification of the immutable v10 training data or weights.
out[cols].to_csv(ROOT/'PHYSICAL_LABEL_REVIEW.csv',index=False)
print(out[[c for c in ('record_id','domain','role','source','target','test_temperature_C','process_label','composition_json') if c in out]].to_json(orient='records',force_ascii=False,indent=2))
