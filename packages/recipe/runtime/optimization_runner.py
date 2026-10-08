from pathlib import Path
import argparse
import json
import os
import uuid
from bounded_nsga import Limits,objectives,search
from candidate_model_stack import NativeSpace,ModelStack,DOMAINS
from optimization_contract import map_resolved_objectives

def execute(item):
    if not isinstance(item,dict) or set(item)-{'model_domain','scope','specialist_result','objectives','limits','seed'}:
        raise ValueError('invalid optimization envelope')
    domain=item.get('model_domain');scope=item.get('scope');task=item.get('specialist_result')
    if domain not in DOMAINS or not isinstance(scope,dict) or not isinstance(task,dict):raise ValueError('unknown domain or incomplete resolved inputs')
    if scope.get('model_domain')!=domain or task.get('model_domain')!=domain:raise ValueError('mixed material domains')
    if domain=='reusable_rocket_stainless' and scope.get('test_temperature_K',0)<293:
        raise ValueError('cryogenic stainless is evidence-only; no compositional optimizer')
    space=NativeSpace(domain,scope,task);stack=ModelStack(space)
    try:
        spec=objectives(item.get('objectives') or map_resolved_objectives(scope,stack.allowed),stack.allowed)
        limits=Limits(**(item.get('limits') or {}));seed=item.get('seed',20260928)
        if isinstance(seed,bool) or not isinstance(seed,int) or not 0<=seed<2**32:raise ValueError('invalid optimization seed')
        out=search(space,stack,spec,limits,seed)
        out['model_domain']=domain;out['model_stack']='alloy_v10/glass_v1 plus retained property specialists'
        out['scope']=scope;out['search_variables']=space.variable;out['anchor_count']=len(space.anchors)
        out['constraints_and_defaults_provenance']='resolved existing specialist scope; no new user goal inferred'
        return out
    finally:stack.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True);parser.add_argument('--response',type=Path,required=True)
    args=parser.parse_args();code=0
    try:
        if args.request.stat().st_size>16*1024*1024:raise ValueError('oversized optimizer request')
        payload=json.loads(args.request.read_text())
        if set(payload)!={'items'} or not isinstance(payload['items'],list) or len(payload['items'])!=1:
            raise ValueError('one optimization task per bounded subprocess required')
        result=execute(payload['items'][0]);out=dict(ok=True,mode='shadow',display_allowed=False,
            production_release_authorized=False,public_effect=False,results=[result])
    except Exception as exc:
        code=2;out=dict(ok=False,mode='shadow',display_allowed=False,production_release_authorized=False,
                       public_effect=False,error_type=type(exc).__name__,error=str(exc))
    args.response.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.response.with_name(args.response.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False));os.replace(tmp,args.response)
    print(json.dumps({k:v for k,v in out.items() if k!='results'}));return code
if __name__=='__main__':raise SystemExit(main())
