from copy import deepcopy
from pathlib import Path
import json
import os
import pytest

from recipe_display import (from_rsi,build_markdown,validate_markdown,embed_assets,
                            from_perovskite,from_input_issue,component_groups,process_text,condition_rows)

HERE=Path(__file__).resolve().parent
EXAMPLES=Path(os.environ.get('RECIPE_TEST_EXAMPLES',str(HERE.parent/'rsi_handoff_20260928')))


@pytest.fixture(params=['stainless','aluminum','glass'])
def actual_report(request):
    case=json.loads((EXAMPLES/(request.param+'_retained_case.json')).read_text(encoding='utf-8'))
    task={'model_domain':case['request']['domain']}
    return from_rsi(case['response'],task,request=case['request'])


def test_real_retained_outputs_keep_all_positive_components(actual_report):
    text=build_markdown(actual_report);validate_markdown(text)
    top=actual_report['candidates'][0]
    for key,amount in top['composition']['values'].items():
        if amount>0:
            if actual_report['domain']=='chip_glass_thermomechanical_family_v1':
                from glass_presentation import oxide_label
                assert '| '+oxide_label(key)+' |' in text
            else:
                assert '（'+key+'）' in text
    assert '随机种子' not in text and '种群规模' not in text
    assert '候选提议上限' not in text and '±0' not in text


def test_technical_settings_retained_but_not_user_visible(actual_report):
    assert actual_report['technical_settings']['limits']['model_evaluations']==256
    assert actual_report['technical_settings']['seed']==20260928
    text=build_markdown(actual_report)
    assert '20,480' not in text and '20260928' not in text


def test_no_input_mutation(actual_report):
    saved=deepcopy(actual_report);build_markdown(actual_report)
    assert actual_report==saved


def test_heading_contract_and_embedding(actual_report):
    text=build_markdown(actual_report)
    names=['recipe_composition_0','recipe_tradeoff']
    assets=[{'name':n,'url':'https://example.org/'+n+'.png','title':'结果图'} for n in names]
    final=embed_assets(text,assets,public=True)
    assert '{{VISUAL:' not in final and '![' in final
    preferred = '优选配方（'+actual_report['candidates'][0]['display_id']+'）'
    assert '#### 5.1 '+preferred+'的完整配比' in final
    assert '#### 5.2 '+preferred+'的完整预测性能' in final
    with pytest.raises(ValueError):embed_assets(text,[{'name':n,'url':'/tmp/'+n+'.png','title':'图'} for n in names],public=True)


def test_private_flags_required(actual_report):
    bad=deepcopy(actual_report);bad['display_allowed']=True
    with pytest.raises(ValueError):build_markdown(bad)


def test_composition_basis_not_inferred_or_repaired(actual_report):
    bad=deepcopy(actual_report);key=next(iter(bad['candidates'][0]['composition']['values']))
    bad['candidates'][0]['composition']['values'][key]+=1
    with pytest.raises(ValueError):build_markdown(bad)


def test_nan_property_rejected(actual_report):
    bad=deepcopy(actual_report);key=next(iter(bad['candidates'][0]['values']))
    bad['candidates'][0]['values'][key]=float('nan')
    with pytest.raises(ValueError):build_markdown(bad)


def test_unknown_source_not_silently_relabelled(actual_report):
    bad=deepcopy(actual_report);key=next(iter(bad['candidates'][0]['property_sources']))
    bad['candidates'][0]['property_sources'][key]='future_model'
    with pytest.raises(ValueError):build_markdown(bad)


def test_empty_front_preserves_no_result_semantics(actual_report):
    empty=deepcopy(actual_report);empty['front']=[];empty['candidates']=[]
    text=build_markdown(empty);validate_markdown(text)
    assert '没有同时满足要求的配方' in text
    assert '本次优选配方：' not in text


def test_single_objective_target_is_not_called_multi_objective(actual_report):
    one=deepcopy(actual_report)
    key=one['objectives'][0]['property']
    one['objectives']=[{'property':key,'mode':'target','target':1000,'unit':'MPa'}]
    text=build_markdown(one)
    assert '尽量接近1000 MPa' in text
    assert '多目标搜索返回' not in text


def test_cf_basis_is_volume_not_weight():
    r={'domain':'short_cf_thermomechanical_rve_v1'}
    groups=component_groups(r,{'composition':{'values':{'target_vf':.3,'fiber_length_mm':.1,'target_a11':.7}}})
    assert groups[0][1]=={'fiber':30,'matrix':70} and groups[0][2]=='vol.%'


def test_perovskite_sites_are_independently_closed():
    r={'domain':'perovskite_transport_stability_v2'}
    groups=component_groups(r,{'composition':{'values':{'Cs':.1,'FA':.8,'MA':.1,'I':.9,'Br':.1}}})
    assert len(groups)==2 and all(sum(c.values())==100 for _,c,_ in groups)


def test_perovskite_uses_actual_service_field_names():
    r={'domain':'perovskite_transport_stability_v2'}
    values={'cs_fraction':.2,'fa_fraction':.8,'ma_fraction':0,'iodide_fraction':.2,'bromide_fraction':.8}
    groups=component_groups(r,{'composition':{'values':values}})
    assert groups[0][1]['Cs']==20 and groups[1][1]['Br']==80


def test_nickel_heat_treatment_is_readable():
    text=process_text('solution_stage_1_temp_C=1302; solution_stage_1_time_h=4; precipitation_stage_1_temp_C=982; precipitation_stage_1_time_h=5')
    assert '1302°C' in text and '保温4 h' in text and '982°C' in text
    assert 'solution_stage' not in text


def test_actual_aluminum_process_name_and_default_temperature():
    rows=condition_rows({'domain':'aerospace_aluminum_composition_v1',
        'conditions':{'processing_category':'Solutionised + Artificially peak aged'},'scope':{}})
    assert any(row[1]=='固溶后人工峰值时效' for row in rows)
    assert any(row[1]=='25°C' for row in rows)


def test_missing_plot_must_not_silently_disappear(actual_report):
    with pytest.raises(ValueError):embed_assets(build_markdown(actual_report),[])


def test_strength_ordering_violation_rejected(actual_report):
    if 'UTS_MPa' not in actual_report['candidates'][0]['values']:return
    bad=deepcopy(actual_report);bad['candidates'][0]['values']['UTS_MPa']=0
    with pytest.raises(ValueError):build_markdown(bad)


def test_input_reference_gap_is_not_faked_as_nsga_run():
    task={'model_domain':'copper_hot_end_local_composition_v1'}
    report=from_input_issue(task,{'scope':{'processing_state':'as_received'}},'NO_ELIGIBLE_STARTING_REFERENCE')
    text=build_markdown(report);validate_markdown(text)
    assert '没有执行NSGA-II' in text and '本次优选配方：' not in text
    assert '220 K' not in text
