"""Bounded RSI subprocess call. Model workers remain in the owned process group."""
from pathlib import Path
import argparse
import json
import os
import sys
import math
import uuid
from private_shadow_dispatcher import run_bounded_process

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',type=Path,required=True);parser.add_argument('--response',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=60.)
    args=parser.parse_args()
    if args.request.resolve()==args.response.resolve():raise ValueError('request and response paths must differ')
    if not math.isfinite(args.timeout) or not 0<args.timeout<=60:raise ValueError('timeout must be in (0,60] seconds')
    command=[sys.executable,str(Path(__file__).with_name('rsi_api.py')),'--request',str(args.request.resolve()),'--response',str(args.response.resolve())]
    try:
        done=run_bounded_process(command,timeout=args.timeout,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
    except Exception as exc:
        # This is a separate bounded-client failure, not a prediction result.
        failure=dict(ok=False,error=dict(code='BOUNDED_EXECUTION_FAILURE',type=type(exc).__name__,message=str(exc)),
                     public_effect=False,display_allowed=False,mode='shadow',production_release_authorized=False)
        args.response.parent.mkdir(parents=True,exist_ok=True)
        temporary=args.response.with_name(args.response.name+'.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(failure,indent=2));os.replace(temporary,args.response)
        print(json.dumps(failure))
        return 2
    print(done.stdout)
    return done.returncode

if __name__=='__main__':raise SystemExit(main())
