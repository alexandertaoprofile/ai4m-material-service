"""Actual model/NSGA/report test for a single requested target."""
from pathlib import Path
import json

from shadow_recipe_runner import execute,build_request
from verify_all_domains import latest_tasks,RUNTIME

HERE=Path(__file__).resolve().parent


def main():
    _,task=latest_tasks()['reusable_rocket_stainless']
    request=build_request(task,limits={'population':8,'model_evaluations':32,'proposals':4096,'seconds':20})
    request['objectives']=[{'property':'YS_MPa','mode':'target','target':1000,'unit':'MPa'}]
    directory=HERE/'single_target_validation'
    response=execute(task,RUNTIME,directory,request=request)
    raw=json.loads((directory/'optimization_response.json').read_text())
    front=raw['result']['pareto_front'];assert front and len(raw['result']['objectives'])==1
    assert all(abs(row['F'][0]-round(abs(row['values']['YS_MPa']-1000),3))<.0011 for row in front)
    text=(directory/'presentation/preview.md').read_text()
    assert '尽量接近1000 MPa' in text and '按本次单目标' in text and '多目标搜索返回' not in text
    output={'verified':True,'actual_method':'nsga2','objective':'abs(YS_MPa - 1000)',
        'evaluations':raw['result']['counters']['model_evaluations'],
        'public_effect':False,'formal_result_mutated':False,'display_allowed':False}
    (HERE/'SINGLE_TARGET_VALIDATION.json').write_text(json.dumps(output,indent=2))
    print(json.dumps(output),flush=True)


if __name__=='__main__':main()
