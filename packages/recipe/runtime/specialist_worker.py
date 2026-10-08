import json
from pathlib import Path
import sys
import traceback
import warnings
from sklearn.exceptions import InconsistentVersionWarning
warnings.filterwarnings('error',category=InconsistentVersionWarning)
from candidate_model_stack import NativeSpace,ModelStack

stack=None
for line in sys.stdin:
    try:
        if len(line)>16*1024*1024:raise ValueError('oversized specialist request')
        payload=json.loads(line)
        if set(payload)=={'init'} and stack is None:
            p=payload['init']
            # The owning stack applies gates ONCE to the final property values.
            stack=ModelStack(NativeSpace(p['domain'],p['scope'],p['task'],reference_policy=p.get('reference_policy','evaluation')),
                             mode='specialist',specialist_local=True,apply_thresholds=False)
            out={'ok':True}
        elif set(payload)=={'evaluate'} and stack is not None:
            if not isinstance(payload['evaluate'],list) or not 1<=len(payload['evaluate'])<=256:raise ValueError('invalid specialist batch')
            out={'ok':True,'rows':stack(payload['evaluate'])}
        else:raise ValueError('invalid specialist bridge protocol')
    except Exception as exc:out={'ok':False,'error':str(exc)}
    print(json.dumps(out,allow_nan=False),flush=True)
