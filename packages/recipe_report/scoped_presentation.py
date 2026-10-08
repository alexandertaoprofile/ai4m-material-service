"""Present a completed specialist assessment without invented predictions."""
import json
import math


def describe(key,value,display):
    aliases={'yield_strength':'YS_MPa','uts':'UTS_MPa','elongation':'elongation_pct',
        'CTE_linear_0_to_300C':'cte_0_300_ppm_K','density_g_per_cm3':'density_g_cm3',
        'rupture_life':'rupture_hours','ultimate_tensile_strength_MPa':'UTS_MPa','proof_strength_0p2_MPa':'YS_MPa'}
    if key=='objectives' and isinstance(value,dict):
        rows=[]
        for prop,goal in value.items():
            prop=aliases.get(prop,prop);label=display.PROPERTIES.get(prop,(prop,))[0]
            if isinstance(goal,dict):
                direction={'maximize':'尽量提高','minimize':'尽量降低','max':'尽量提高','min':'尽量降低'}.get(goal.get('goal'))
                if goal.get('goal')=='target':direction='尽量接近 '+str(goal.get('target'))+' '+str(goal.get('unit',''))
            else:direction='按已设权重比较'
            rows.append(label+'：'+(direction or '保留原目标'))
        return '；'.join(rows) or '按已支持性质比较'
    if isinstance(value,dict) and ('bounds' in key or key=='candidate_windows'):
        labels={'target_vf':'碳纤维体积分数','fiber_length_mm':'有效纤维长度（mm）','target_a11':'主方向取向参数'}
        rows=[]
        for item,interval in value.items():
            label=labels.get(item,display.NAMES.get(item,item))
            if isinstance(interval,(list,tuple)) and len(interval)==2:
                factor=100 if item=='target_vf' else 1
                rows.append(label+'：'+ '–'.join(display.number(v*factor) if isinstance(v,(int,float)) and math.isfinite(v) else '数值待核对' for v in interval)+('%' if item=='target_vf' else ''))
            else:rows.append(label+'：'+str(interval))
        return '；'.join(rows)
    return json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value)


def conclusion(report, display):
    name=display.DOMAINS[report['domain']][0]
    details=report['scoped_outcome']
    reasons='；'.join(details['reasons'])
    return (f'本次仍由{name}专项路线处理。{reasons}\n\n'
        '原配比范围与性能要求保持不变。本次尚无可交付的配方预测值，也未判定存在全部达标方案。'
        '后续可在本路线比较调整条件后的结果，或补充原范围内的参考数据。')


def markdown(report, display):
    from report_outline import HEADINGS
    scope=report.get('requested_scope') or report['scope']
    info=report['scoped_outcome'];name=display.DOMAINS[report['domain']][0]
    labels={'element_bounds_wt_percent':'元素含量范围（wt.%）','element_bounds_at_pct':'元素含量范围（at.%）',
        'oxide_bounds_mol_percent':'氧化物含量范围（mol.%）','candidate_windows':'复材配比与结构参数范围',
        'test_temperature_C':'评价温度（°C）','test_temperature_K':'评价温度（K）',
        'temperatures_K':'评价温度（K）','objectives':'性能优化目标','screening_thresholds':'性能门槛',
        'processing':'材料处理状态','processing_category':'处理类别','matrix_name':'基体材料',
        'heat_treatment':'热处理制度','manufacturing_route':'制造路线','applied_stress_MPa':'施加应力（MPa）'}
    rows=[(label,describe(key,scope[key],display))
        for key,label in labels.items() if key in scope]
    reasons=[(reason,'原要求保留，尚无符合本项条件的预测结论') for reason in info['reasons']]
    return '\n\n'.join([HEADINGS[0],f'本次继续通过{name}专项路线核对配方与性能要求。',
        '用户需求：'+str(info.get('request') or '按本次给定的结构化条件处理。'),
        HEADINGS[1],display.table(['本次条件','采用或保留的要求'],rows),
        HEADINGS[2], '本次完成材料路线与计算条件核对，以下保留原始要求及具体限制。',
        display.equation(r'\mathcal D_{\rm usable}=\mathcal D_{\rm requested}\cap\mathcal D_{\rm model}'),
        r'$\mathcal D_{\rm requested}$为用户给定的配比与工况范围，$\mathcal D_{\rm model}$为当前模型支持范围。'
        '范围有交集时在交集内继续优化；没有交集时，报告具体限制并保留原要求。',
        HEADINGS[3],display.table(['当前情况','要求满足情况'],reasons),
        HEADINGS[4],'本次尚无可列出的优选配方与预测性能。可优先补充原范围内的成分、状态与性能数据；'
        '如需比较模型支持范围内的配方，可另列参考方案，并注明与本次要求的差异。',
        '### 6. 优化结论',conclusion(report,display)])


def deferred_table(report, display):
    scope=report.get('requested_scope') or report.get('scope') or {}
    items=scope.get('_uncomputed_requirements',[])
    if not items:return ''
    return ('#### 尚未计算的要求\n\n'+display.table(['保留的用户要求','本次处理'],
        [(item,'该项暂不可计算；其余已支持性质继续优化。') for item in items])+
        '\n\n以下结果仅评价已计算的性质，尚未判定全部用户要求均已满足。')
