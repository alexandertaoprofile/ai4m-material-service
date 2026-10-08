"""Read-only physical-label checks and diagnostic support; no threshold is relaxed
to manufacture online admission. No new calibrated-domain claim is made.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist
ROOT=Path(__file__).resolve().parent
frame=pd.read_csv(ROOT/'frozen_data/alloy_v8.csv',low_memory=False)
profile={};label_audit={}
for domain,group in frame.groupby('domain'):
    comp=group.composition_json.map(json.loads);all_elements=sorted(set().union(*(x.keys() for x in comp)))
    closure=max(abs(sum(c.values())-1) for c in comp)
    stem=group.assign(stem=group.record_id.str.replace(r':(?:YS|UTS)$','',regex=True))
    pairs=stem.pivot_table(index=['stem','role'],columns='task',values='target',aggfunc='first').dropna(subset=['YS','UTS'])
    bad=pairs[pairs.YS>pairs.UTS+1e-6]
    label_audit[domain]=dict(task_rows=len(group),max_atomic_fraction_closure_error=float(closure),
        nonpositive_labels=int(group.target.le(0).sum()),paired_strength_records=len(pairs),
        observed_ys_above_uts=len(bad),ys_above_uts_examples=bad.reset_index().head(10).to_dict('records'),
        paired_label_audit_complete=len(pairs)>0,
        limitation='Ni YS/UTS record IDs differ; zero matched pairs is NOT evidence of zero violations' if domain=='nickel_superalloy' else None)
    train=group.loc[group.role.eq('train')].drop_duplicates('composition_key')
    tc=train.composition_json.map(json.loads).tolist()
    X=np.asarray([[c.get(e,0) for e in all_elements] for c in tc]);D=cdist(np.sqrt(X),np.sqrt(X))/np.sqrt(2)
    np.fill_diagonal(D,np.inf);nearest=D.min(axis=1);full95=float(np.quantile(nearest,.95))
    main_elements=[e for e in all_elements if e not in {'P','S','N'}] if domain=='stainless' else all_elements
    main=X[:,[all_elements.index(e) for e in main_elements]];main/=main.sum(axis=1,keepdims=True)
    Dm=cdist(np.sqrt(main),np.sqrt(main))/np.sqrt(2);np.fill_diagonal(Dm,np.inf)
    main95=float(np.quantile(Dm.min(axis=1),.95))
    profile[domain]=dict(training_compositions=len(train),elements=all_elements,
        full_hellinger_training_loo_95_percentile=full95,major_diagnostic_training_loo_95_percentile=main95,
        major_diagnostic_exclusions=['N','P','S'] if domain=='stainless' else [],
        major_distance_does_not_approve_trace_elements=True,
        test_temperature_C_training_range=[float(train.test_temperature_C.min()),float(train.test_temperature_C.max())],
        service_comparisons=[])
    channel={'aluminum':'alloy_v10','stainless':'alloy_v10_stainless','nickel_superalloy':'alloy_v10_nickel'}.get(domain)
    if not channel:continue
    responses=sorted((ROOT/'private_e2e_results'/('e2e-'+channel)/'shadow'/channel).glob('*/response.json'),key=lambda p:p.stat().st_mtime)
    if not responses:continue
    data=json.loads(responses[-1].read_text())
    for item in data.get('comparisons',[]):
        audit=item.get('shadow_result',{}).get('input_audit',{})
        composition=audit.get('atomic_fractions')
        if not composition:continue
        c=np.asarray([composition.get(e,0) for e in all_elements]);full=cdist(np.sqrt(c)[None,:],np.sqrt(X))/np.sqrt(2)
        m=c[[all_elements.index(e) for e in main_elements]];m/=m.sum()
        major=cdist(np.sqrt(m)[None,:],np.sqrt(main))/np.sqrt(2)
        profile[domain]['service_comparisons'].append(dict(candidate_id=item.get('candidate_id'),
            full_distance=float(full.min()),full_distance_pass=bool(full.min()<=full95),
            major_diagnostic_distance=float(major.min()),major_diagnostic_pass=bool(major.min()<=main95),
            process_oov=audit.get('process_id')==0,process_conflicts=audit.get('process_conflicts'),
            known_temperature=not audit.get('temperature_missing',True),
            transformer_empirical_support_not_approved=True))
out=dict(label_audit=label_audit,support_diagnostics=profile,production_release_authorized=False,
    policy='do not substitute major-only distance for scientific admission; trace reporting and numeric process coverage remain independent gates',
    uncertainty_policy='single checkpoint has no calibrated candidate uncertainty; never print ensemble std or +/-0')
(ROOT/'SUPPORT_AND_LABEL_AUDIT.json').write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False))
print(json.dumps(dict(label_audit=label_audit,service_coverage={d:dict(compared=len(p['service_comparisons']),
    full_pass=sum(x['full_distance_pass'] for x in p['service_comparisons']),major_diagnostic_pass=sum(x['major_diagnostic_pass'] for x in p['service_comparisons']),
    process_oov=sum(x['process_oov'] for x in p['service_comparisons'])) for d,p in profile.items()}),ensure_ascii=False))
