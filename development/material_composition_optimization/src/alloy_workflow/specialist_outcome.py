"""Same-route reports when a supported specialist has no computable recipe."""
from copy import deepcopy
from src.alloy_workflow.contracts import task_id


def scoped_outcome(payload, scope, plan, service, outcome, reasons, *, seed=None, diagnostic=None):
    from src.alloy_workflow.recipe_primary import helper, downstream_summary
    domain=scope['model_domain'];display=helper('recipe_display')
    if domain not in display.DOMAINS:
        raise ValueError('a scoped outcome requires an adapted material domain')
    task=deepcopy(seed or {})
    task.update(model_domain=domain,requirement_interpretation=deepcopy(plan))
    report=display.from_input_issue(task, {'scope':deepcopy(scope)}, outcome)
    report['scoped_outcome']={'kind':outcome,'reasons':list(reasons),'request':plan.get('raw_requirement','')}
    report['requested_scope']=deepcopy(scope)
    # Inverted legacy seed windows describe an empty intersection, not a valid
    # search interval. Preserve the actual requested interval separately.
    if seed and outcome=='no_supported_reference':
        windows=seed.get('screening_conditions',{}).get('local_candidate_windows',{})
        wanted=scope.get('candidate_windows',{})
        for key,(lo,hi) in windows.items():
            if lo>hi and key=='target_vf' and key in wanted:
                report['scoped_outcome']['reasons'].append(
                    f'本次要求碳纤维体积分数为{wanted[key][0]*100:g}%–{wanted[key][1]*100:g}%，'
                    f'当前模型参考窗口的上限为{hi*100:g}%，二者没有交集。')
    conclusion=downstream_summary(report,task_id(payload),display)
    result={'taskid':task_id(payload),'status':'completed','service':service,'model_domain':domain,
        'requirement_interpretation':deepcopy(plan),'initial_candidates':[],'nearest_candidates':[],
        'all_candidates':[],'sampling':{'generated':0,'feasible':0,'selected':0},
        'screening_conditions':deepcopy(report['conditions']),
        'recipe_release':{'mode':'primary','optimizer':'input_validation','property_sources':{},
            'version':'specialist_route_continuation_v1','public_effect':True,'display_allowed':True},
        'model_version':'specialist_route_continuation_v1','user_conclusion':conclusion,
        'specialist_outcome':deepcopy(report['scoped_outcome']),
        'elapsed_seconds':0.0,'_recipe_report':report}
    if diagnostic:result['calculation_diagnostic']=diagnostic
    return result,{'taskid':result['taskid'],'model_domain':domain,'raw_scope':deepcopy(scope)}
