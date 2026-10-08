from copy import deepcopy
import json
import os
from pathlib import Path
import pytest

from recipe_display import from_rsi, from_input_issue, build_markdown, validate_markdown, embed_assets, property_value
from glass_presentation import DOMAIN, HEADINGS, oxide_label, chart_caption, content_amount


@pytest.fixture
def glass():
    from test_recipe_display import EXAMPLES
    case = json.loads((EXAMPLES/'glass_retained_case.json').read_text())
    return from_rsi(case['response'], {'model_domain':DOMAIN}, request=case['request'])


def test_public_report_keeps_all_predictions_and_does_not_mutate(glass):
    before = deepcopy(glass)
    text = build_markdown(glass)
    assert glass == before
    for key, value in glass['candidates'][0]['values'].items():
        _, formatted, unit = property_value(key, value)
        assert formatted + ' ' + unit in text
    for key, value in glass['candidates'][0]['composition']['values'].items():
        if value > 0:
            assert '| '+oxide_label(key)+' | '+content_amount(value)+' |' in text
    assert 'GLS001（优选配方）' in text and 'GLS005' in text
    for word in ['GLS-001', '性质卡', '筛选结果', '| 标识 |', '计算来源', '问题描述', '输出定义及验证路径']:
        assert word not in text
    assert '方案一' not in text
    assert '优选配方（GLS001）的完整配比' in text and '优选配方（GLS001）的完整预测性能' in text
    assert all(word not in text for word in ['不是','不代表','并非','不等于','不能代替'])
    assert '本页性能为模型预测' in text and '试制并测量目标性能' in text
    assert all(text.count(heading) == 1 for heading in HEADINGS)
    assert 'mol.%' in text and '按物质的量计算' in text


def test_empty_and_input_invalid_do_not_claim_recommendation(glass):
    glass.update(candidates=[], front=[], nearest_candidates=[])
    text=build_markdown(glass)
    validate_markdown(text)
    assert '没有同时满足要求' in text
    assert '{{VISUAL:' not in text and '方案一（优选配方）' not in text
    issue=from_input_issue({'model_domain':DOMAIN}, {'scope':{}}, 'NO_ELIGIBLE_STARTING_REFERENCE')
    text=build_markdown(issue)
    validate_markdown(text)
    assert '没有执行NSGA-II' in text and '未执行配方优化' in text
    assert '{{VISUAL:' not in text


def test_nearest_is_not_described_as_feasible(glass):
    row=deepcopy(glass['candidates'][0])
    row.update(feasible=False, admissible=True)
    observed=row['values']['young_modulus_GPa']
    row['constraint_gaps']={'modulus':{'property':'young_modulus_GPa', 'direction':'min', 'observed':observed,
        'target':observed+10, 'shortfall':10}}
    glass.update(candidates=[], front=[], nearest_candidates=[row])
    text=build_markdown(glass)
    validate_markdown(text)
    assert '仍差 10.00 GPa' in text and '没有同时满足全部性能要求' in text
    assert '优选配方' not in text
    assert '接近目标配方' in chart_caption(glass)


def test_target_and_changed_axes_are_described_from_actual_goals(glass):
    glass['objectives']=[{'property':'density_g_cm3','mode':'target','target':2.5,'unit':'g/cm³'}]
    text=build_markdown(glass)
    assert '尽量接近2.5 g/cm³' in text
    assert '本次为单目标优化' in text
    assert '虚线表示指定目标值' in text
    assert '横轴对应上表中的配方编号' in text
    glass['objectives']=[{'property':'young_modulus_GPa','mode':'max','unit':'GPa'},
                         {'property':'density_g_cm3','mode':'min','unit':'g/cm³'}]
    caption=chart_caption(glass)
    assert '横轴为弹性模量' in caption and '纵轴为密度' in caption
    assert '色条' not in caption


def test_micro_components_fixed_bounds_and_unknown_oxides_are_not_hidden(glass):
    glass['resolved_design']['bounds']={'SiO2':[65,75],'Li2O':[0,0]}
    c=glass['candidates'][0]['composition']['values']
    c['SiO2']-=1e-8
    c['Y2O3']=1e-8
    text=build_markdown(glass)
    assert '| Y₂O₃ | 1e-08 |' in text
    assert '上下限均为0表示本次设计不加入' in text
    assert oxide_label('Fe2O3')=='氧化铁（Fe₂O₃）'


def test_missing_sections_or_assets_fail_closed(glass):
    text=build_markdown(glass)
    for heading in HEADINGS[1:]:
        with pytest.raises(ValueError): validate_markdown(text.replace(heading,''))
    with pytest.raises(ValueError): embed_assets(text, [], public=True)


def test_other_domain_report_uses_same_outline_without_numerical_change():
    examples=Path(os.environ['RECIPE_TEST_EXAMPLES'])
    for domain in ['stainless','aluminum']:
        case=json.loads((examples/(domain+'_retained_case.json')).read_text())
        report=from_rsi(case['response'], {'model_domain':case['request']['domain']}, request=case['request'])
        before=deepcopy(report)
        text=build_markdown(report)
        assert report==before
        assert all(h in text for h in HEADINGS)


def test_generic_outline_is_shared_and_preserves_legacy_validation(glass):
    from report_outline import HEADINGS as common, LEGACY_GLASS_HEADINGS
    assert HEADINGS is common
    assert all('玻璃' not in h for h in common)
    text=build_markdown(glass)
    validate_markdown(text)
    for current, old in zip(common, LEGACY_GLASS_HEADINGS):
        text=text.replace(current,old)
    validate_markdown(text)
    with pytest.raises(ValueError):validate_markdown(text.replace(LEGACY_GLASS_HEADINGS[-1],''))


def test_formulas_have_matched_delimiters_and_explanations(glass):
    text=build_markdown(glass)
    assert text.count('$$')==14
    for formula in [r'\sum_{i=1}^{n}c_i=100',r'\widehat y_t=f_{\theta}',r'H_{\rm comp}',
                    r'F_j(\mathbf c)=\begin{cases}',r'\min_{\mathbf c\in\mathcal D}',
                    r'J(\mathbf c)=',r'\Delta\varepsilon_{\rm th}']:
        assert formula in text
    for explanation in ['含量下限和含量上限','温度是否有记录','按优化器比较精度舍入后的预测值',
                        '当前等权规则','该分数用于本次配方的推荐排序','自由热应变之差']:
        assert explanation in text
    glass['objectives']=[{'property':'young_modulus_GPa','mode':'max','unit':'GPa'}]
    assert r'J(\mathbf c)=' not in build_markdown(glass)
