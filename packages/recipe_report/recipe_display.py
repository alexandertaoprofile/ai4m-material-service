"""Shared, data-faithful report model for private material recipe results.

Rendering never invokes training, inference, object storage or a public route.
The same tokenized Markdown can later use the existing 1111 asset publisher.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import re

VERSION = 'material_recipe_display_v5_unified_20260929'
DOMAINS = {
    'hea_mpea': ('高熵与多主元合金', 'at.%', 'HEA'),
    'ni_superalloy_hot_end': ('镍基高温合金', 'wt.%', 'NI'),
    'copper_hot_end_local_composition_v1': ('铜基热壁合金', 'wt.%', 'CU'),
    'reusable_rocket_stainless': ('奥氏体不锈钢', 'wt.%', 'RSS'),
    'aerospace_aluminum_composition_v1': ('航空铝合金', 'wt.%', 'ALU'),
    'chip_glass_thermomechanical_family_v1': ('芯片封装玻璃', 'mol.%', 'GLS'),
    'short_cf_thermomechanical_rve_v1': ('短碳纤维增强热塑性复合材料', 'vol.%', 'CF'),
    'perovskite_transport_stability_v2': ('卤化物钙钛矿', 'site_fraction', 'PV'),
}
NAMES = {'Fe':'铁','Cr':'铬','Ni':'镍','Mn':'锰','Si':'硅','C':'碳','N':'氮','P':'磷','S':'硫',
 'Al':'铝','Cu':'铜','Mg':'镁','Zn':'锌','Zr':'锆','Sc':'钪','Li':'锂','Ag':'银','B':'硼',
 'Co':'钴','Sn':'锡','Ti':'钛','V':'钒','Mo':'钼','Nb':'铌','W':'钨','Ta':'钽','Re':'铼','Hf':'铪',
 'SiO2':'二氧化硅','Al2O3':'氧化铝','B2O3':'氧化硼','MgO':'氧化镁','CaO':'氧化钙','BaO':'氧化钡',
 'SrO':'氧化锶','Na2O':'氧化钠','K2O':'氧化钾','Li2O':'氧化锂','P2O5':'五氧化二磷',
 'TiO2':'二氧化钛','SnO2':'二氧化锡','ZnO':'氧化锌','ZrO2':'氧化锆','Cs':'铯','FA':'甲脒阳离子','MA':'甲胺阳离子',
 'I':'碘','Br':'溴','fiber':'碳纤维','matrix':'聚合物基体'}
PROPERTIES = {
 'YS_MPa': ('0.2%屈服强度','MPa',1), 'UTS_MPa': ('抗拉强度','MPa',1),
 'elongation_pct': ('延伸率','%',2), 'rupture_hours': ('蠕变断裂寿命','h',2),
 'hardness_HV': ('维氏硬度','HV',1), 'phase_risk': ('金属间化合物相关相概率','%',2),
 'cte_0_300_ppm_K': ('0–300°C平均线膨胀系数','ppm/K',4),
 'density_g_cm3': ('密度','g/cm³',4), 'young_modulus_GPa': ('杨氏模量','GPa',2),
 'stress_optical_coefficient_nm_cm_per_MPa': ('应力光学系数','nm/(cm·MPa)',3),
 'viscosity_temperature_200_poise_C': ('黏度200 poise对应温度','°C',1),
 'viscosity_temperature_35kpoise_C': ('黏度35,000 poise对应温度','°C',1),
 'E11_MPa': ('主方向弹性模量 E₁₁','GPa',3), 'E22_MPa': ('横向弹性模量 E₂₂','GPa',3),
 'E33_MPa': ('厚度方向弹性模量 E₃₃','GPa',3), 'G12_MPa': ('剪切模量 G₁₂','GPa',3),
 'G13_MPa': ('剪切模量 G₁₃','GPa',3), 'G23_MPa': ('剪切模量 G₂₃','GPa',3),
 'V12': ('泊松比 ν₁₂','无量纲',4), 'V13': ('泊松比 ν₁₃','无量纲',4),
 'V23': ('泊松比 ν₂₃','无量纲',4), 'fiber_volume_fraction': ('碳纤维体积分数','%',3),
 'anisotropy_ratio_E11_E22': ('主方向/横向模量比 E₁₁/E₂₂','无量纲',3),
 'ln_sigmaT_220K': ('220 K电输运响应 ln[σT]','数据定义单位',3),
 'ln_sigmaT_300K': ('300 K电输运响应 ln[σT]','数据定义单位',3),
 'Ea_low_eV': ('低温区有效活化能','eV',4), 'Ea_high_eV': ('高温区有效活化能','eV',4),
}


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('report needs finite numeric model outputs')
    return float(value)


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\r', '').replace('\n', '<br>')
    return '\n'.join(['| '+' | '.join(map(cell, headers))+' |',
                      '| '+' | '.join(['---']*len(headers))+' |'] +
                     ['| '+' | '.join(map(cell,row))+' |' for row in rows])


def equation(text):
    return '$$\n'+text+'\n$$'


def number(value):
    return f'{finite(value):.6f}'.rstrip('0').rstrip('.') or '0'


def property_value(key, value):
    label, unit, digits = PROPERTIES.get(key, (key,'',3))
    value = finite(value)
    if key.endswith('_MPa') and key[0] in {'E','G'}: value /= 1000
    if key in {'fiber_volume_fraction','phase_risk'}: value *= 100
    return label, f'{value:.{digits}f}', unit


def private_flags(value):
    if not isinstance(value, dict) or any(value.get(k) is not False for k in
        ('public_effect','display_allowed','production_release_authorized')):
        raise ValueError('report requires explicit private flags')


def from_rsi(response, task, *, request=None):
    private_flags(response)
    if response.get('ok') is not True or response.get('operation') != 'optimize':
        raise ValueError('only completed optimization results may claim an optimizer')
    result = response['result']; domain = result['domain']
    if domain not in DOMAINS or task.get('model_domain',domain) != domain:
        raise ValueError('report domain does not match task')
    private_flags(result)
    specs = result['objectives']; front = deepcopy(result['pareto_front'])
    if front:
        count = len(specs)
        if any(len(row['F']) != count or row.get('feasible') is not True for row in front):
            raise ValueError('front schema or feasibility mismatch')
        lo = [min(finite(row['F'][i]) for row in front) for i in range(count)]
        span = [max(finite(row['F'][i]) for row in front)-lo[i] for i in range(count)]
        def score(row):
            return sum((finite(row['F'][i])-lo[i])/span[i] if span[i]>1e-12 else 0 for i in range(count))/count
        front.sort(key=lambda row:(score(row), json.dumps(row['composition'], sort_keys=True)))
        for i,row in enumerate(front):
            row['display_id'] = DOMAINS[domain][2]+f'{i+1:03d}'
            row['preference_score'] = score(row)
    nearest = deepcopy(result.get('nearest_candidates',[])) if not front else []
    for i,row in enumerate(nearest,1):
        if row.get('feasible') is not False or row.get('admissible') is not True or not row.get('constraint_gaps'):
            raise ValueError('nearest recipe needs valid inputs and explicit unmet performance gates')
        row['display_id']=DOMAINS[domain][2]+f'{i:03d}'
    return {'domain':domain,'objectives':deepcopy(specs),'candidates':front[:5],
        'nearest_candidates':nearest,
        'front':front,'history':deepcopy(result['history']), 'method':result['method'],
        'stop_reason':result.get('stop_reason'),'counters':deepcopy(result['counters']),
        'elapsed_seconds':response.get('elapsed_seconds'),
        'conditions':deepcopy(result.get('material_conditions',{})),
        'scope':deepcopy(result.get('scope',{})),
        'resolved_design':deepcopy(response.get('resolved_design',{})),
        'execution_notes':deepcopy((task.get('requirement_interpretation') or {}).get('execution_notes',[])),
        'provenance':deepcopy((task.get('requirement_interpretation') or {}).get('field_provenance',{})),
        'requested_scope':deepcopy((task.get('requirement_interpretation') or {}).get('effective_model_input',{})),
        'model_metadata':{'weights_sha256':response.get('weights_sha256'),
                          'code_tree_sha256':response.get('code_tree_sha256')},
        'technical_settings':deepcopy(request or {}), 'public_effect':False,
        'display_allowed':False,'production_release_authorized':False}


def from_perovskite(task):
    """Keep documented site-grid comparison distinct from NSGA-II."""
    if task.get('model_domain') != 'perovskite_transport_stability_v2':
        raise ValueError('expected perovskite task')
    candidates=[]
    for index,row in enumerate(task.get('initial_candidates',[])[:5],1):
        values={key:finite(row[key]) for key in ('ln_sigmaT_220K','ln_sigmaT_300K','Ea_low_eV','Ea_high_eV')}
        candidates.append({'display_id':f'PV{index:03d}','values':values,
            'composition':{'values':deepcopy(row['composition'])},'formula':row['formula'],
            'curve':deepcopy(row['predicted_transport'].get('curve',[])),
            'property_sources':{k:'perovskite_transport_v2' for k in values},'feasible':True})
    return {'domain':task['model_domain'],'objectives':[
        {'property':'ln_sigmaT_220K','mode':'min','unit':'data_defined'},
        {'property':'ln_sigmaT_300K','mode':'min','unit':'data_defined'}],
        'candidates':candidates,'front':[], 'history':candidates,
        'method':'documented_grid','conditions':deepcopy(task.get('screening_conditions',{})),
        'scope':deepcopy((task.get('requirement_interpretation') or {}).get('effective_model_input',{})),
        'execution_notes':deepcopy((task.get('requirement_interpretation') or {}).get('execution_notes',[])),
        'provenance':deepcopy((task.get('requirement_interpretation') or {}).get('field_provenance',{})),
        'counters':{'model_evaluations':(task.get('sampling') or {}).get('generated',65),
                    'feasible':len(candidates),'pareto_count':0},
        'stop_reason':'documented_grid','model_metadata':{'version':task.get('model_version')},
        'technical_settings':{}, 'public_effect':False,'display_allowed':False,'production_release_authorized':False}


def from_input_issue(task, request, code):
    """A checked input limitation is not a completed model/optimizer run."""
    return {'domain':task['model_domain'],'objectives':[], 'candidates':[],
        'front':[], 'history':[], 'method':'input_validation','input_issue':code,
        'conditions':deepcopy(task.get('screening_conditions') or {}),
        'scope':deepcopy(request['scope']),
        'execution_notes':deepcopy((task.get('requirement_interpretation') or {}).get('execution_notes',[])),
        'provenance':deepcopy((task.get('requirement_interpretation') or {}).get('field_provenance',{})),
        'counters':{'model_evaluations':0,'pareto_count':0},
        'stop_reason':'input_validation','model_metadata':{},
        'technical_settings':deepcopy(request),'public_effect':False,
        'display_allowed':False,'production_release_authorized':False}


def model_source(domain, source, key=None):
    if source == 'alloy_v10': return '统一合金 Transformer v10'
    if source == 'glass_v1_strict': return '玻璃 Transformer strict v1'
    if source == 'perovskite_transport_v2': return '钙钛矿输运模型 v2'
    if source in {'specialist','hea_auxiliary'}:
        if key=='fiber_volume_fraction':return '配方设计输入'
        if key=='anisotropy_ratio_E11_E22':return '由工程常数 E₁₁/E₂₂ 计算'
        return DOMAINS[domain][0]+'专项模型'
    raise ValueError('unknown model provenance: '+str(source))


def provenance(report, key):
    value=report.get('provenance',{}).get(key)
    return {'platform_default':'平台默认，可修改','upstream_context':'承接上文，可修改',
            'user_input':'用户指定','user':'用户指定','text_extraction':'从本次要求提取',
            'template_inference':'平台默认，可修改','material_identity':'按材料对象选择',
            'adopted_assumption':'本次采用，依据见下表',
            'explicit':'用户指定','default':'平台默认，可修改'}.get(value,
            '本次已解析条件，可修改')


def process_text(stages):
    if isinstance(stages,str):
        entries={k:float(v) for k,v in re.findall(r'([A-Za-z_0-9]+)\s*=\s*([0-9.]+)',stages)}
    else: entries=stages or {}
    output=[]
    for kind,label in (('solution','固溶'),('precipitation','时效'),('aging','时效')):
        for index in range(1,5):
            stem=f'{kind}_stage_{index}_';t=entries.get(stem+'temp_C');time=entries.get(stem+'time_h')
            if t is None and time is None:continue
            line=f'第{index}段{label}'
            if t is not None:line+=' '+number(t)+'°C'
            if time is not None:line+='，保温'+number(time)+' h'
            output.append(line)
    return '；'.join(output) or '按已解析的热处理条件计算'


def condition_rows(report):
    c=report['conditions'];s=report.get('scope',{});domain=report['domain'];rows=[]
    if c.get('test_temperature_K') is not None:
        t=finite(c['test_temperature_K']);rows.append(('拉伸评价温度',f'{number(t)} K（约{t-273.15:.1f}°C）',provenance(report,'test_temperature_K')))
    elif c.get('test_temperature_C',s.get('test_temperature_C')) is not None:
        rows.append(('性能评价温度',number(c.get('test_temperature_C',s.get('test_temperature_C')))+'°C',provenance(report,'test_temperature_C')))
    if domain=='aerospace_aluminum_composition_v1':
        if not any('温度' in row[0] for row in rows):
            rows.append(('性能评价温度','25°C','当前铝合金模型的室温任务定义'))
        value=c.get('processing_category',s.get('processing_category','当前目录未收录'))
        labels={'solutionized_artificially_peak_aged':'固溶后人工峰值时效','solutionized_artificially_overaged':'固溶后人工过时效',
                'solutionized_naturally_aged':'固溶后自然时效','annealed':'退火','as_cast':'铸态',
                'solutionised + artificially peak aged':'固溶后人工峰值时效',
                'solutionised + artificially overaged':'固溶后人工过时效',
                'solutionised + naturally aged':'固溶后自然时效'}
        rows += [('材料处理类别',labels.get(str(value).casefold(),value),provenance(report,'processing_category'))]
    elif domain=='reusable_rocket_stainless':
        p=c.get('processing',{});text='固溶退火'
        if p.get('solution_treatment_temperature_K') is not None:
            t=finite(p['solution_treatment_temperature_K']);text+=f'：{t-273.15:.1f}°C'
        if p.get('solution_treatment_time_s') is not None:text+=f'，保温{finite(p["solution_treatment_time_s"])/3600:g} h'
        medium=p.get('cooling_method',p.get('cooling_medium',p.get('quench')))
        if medium:text+='，'+{'water':'水淬','air':'空冷','oil':'油淬'}.get(medium,str(medium))
        rows.append(('处理状态',text,provenance(report,'processing')))
    elif domain=='ni_superalloy_hot_end':
        route=c.get('manufacturing_route',s.get('manufacturing_route','当前目录未收录'))
        rows.append(('制造路线',{'cast':'铸造','single_crystal':'单晶','directionally_solidified':'定向凝固'}.get(route,route),provenance(report,'manufacturing_route')))
        stages=c.get('heat_treatment',s.get('heat_treatment',{}))
        if stages: rows.append(('热处理制度',process_text(stages),provenance(report,'heat_treatment')))
        if c.get('applied_stress_MPa') is not None: rows.append(('蠕变试验应力',number(c['applied_stress_MPa'])+' MPa',provenance(report,'applied_stress_MPa')))
    elif domain=='copper_hot_end_local_composition_v1':
        value=c.get('processing_state',s.get('processing_state','当前目录未收录'))
        rows.append(('材料处理状态',{'as_received':'交付态','solutionized':'固溶态','aged':'时效态'}.get(value,value),provenance(report,'processing_state')))
    elif domain=='hea_mpea':
        value=c.get('processing_method',s.get('processing_method','当前目录未收录'))
        rows.append(('材料制备工艺',{'cast':'铸态','annealed':'退火态','wrought':'变形加工态'}.get(str(value).casefold(),value),provenance(report,'processing_method')))
    elif domain=='chip_glass_thermomechanical_family_v1':
        rows += [('线膨胀系数定义','0–300°C平均线膨胀系数','性质任务定义'),
                 ('密度评价条件','20°C','性质任务定义'),('配方调整范围','同家族记录配方的局部氧化物调整','可修改上下限')]
    elif domain=='short_cf_thermomechanical_rve_v1':
        rows.append(('基体材料',s.get('matrix_name',c.get('matrix_name','以实际RVE输入为准')),provenance(report,'matrix_name')))
    elif domain=='perovskite_transport_stability_v2':
        rows.append(('比较温度','220 K、300 K','当前输运任务定义'))
    return rows


def component_groups(report, candidate):
    domain=report['domain'];c=candidate['composition']['values']
    if domain=='short_cf_thermomechanical_rve_v1':
        vf=finite(c['target_vf'])
        if not 0<=vf<=1: raise ValueError('fiber volume fraction outside [0,1]')
        return [('增强相与基体',{'fiber':vf*100,'matrix':(1-vf)*100},'vol.%')]
    if domain=='perovskite_transport_stability_v2':
        aliases={'Cs':'cs_fraction','FA':'fa_fraction','MA':'ma_fraction','I':'iodide_fraction','Br':'bromide_fraction'}
        a={k:finite(c.get(k,c.get(aliases[k])))*100 for k in ('Cs','FA','MA') if k in c or aliases[k] in c}
        x={k:finite(c.get(k,c.get(aliases[k])))*100 for k in ('I','Br') if k in c or aliases[k] in c}
        if not a or not x: raise ValueError('perovskite needs explicit A and X site fractions')
        groups=[('A位阳离子',a,'mol.%'),('卤素位',x,'mol.%')]
    else: groups=[('配方组成',{k:finite(v) for k,v in c.items()},DOMAINS[domain][1])]
    for _,values,_ in groups:
        if any(v<0 for v in values.values()) or not math.isclose(math.fsum(values.values()),100,abs_tol=.001):
            raise ValueError('report composition does not close in its declared basis')
    return groups


def build_markdown(report):
    import sys
    from scoped_presentation import markdown, deferred_table
    if report.get('scoped_outcome'):
        return markdown(report,sys.modules[__name__])
    text=_build_markdown(report)
    deferred=deferred_table(report,sys.modules[__name__])
    if deferred:
        from report_outline import HEADINGS
        text=text.replace(HEADINGS[1],deferred+'\n\n'+HEADINGS[1],1)
    return text


def _build_markdown(report):
    from recipe_conclusion import build_conclusion
    import sys
    conclusion = build_conclusion(report, sys.modules[__name__])
    from report_outline import HEADINGS
    from parameter_usage import parameter_table
    from recipe_labels import preferred_label, chart_caption, property_meaning
    private_flags(report);domain=report['domain'];name,basis,_=DOMAINS[domain]
    nearest=not report['candidates'] and bool(report.get('nearest_candidates'))
    candidates=report['candidates'] or report.get('nearest_candidates',[]);goals=report['objectives'];sources={}
    for row in report.get('history',[])+candidates:
        for key,value in row['values'].items():
            finite(value)
            sources[key]=model_source(domain,row['property_sources'][key],key)
        if (row.get('feasible') is True or row.get('admissible') is True) and 'YS_MPa' in row['values'] and 'UTS_MPa' in row['values']:
            if row['values']['UTS_MPa']<row['values']['YS_MPa'] or row['values']['YS_MPa']<0:
                raise ValueError('strength ordering violation in display input')
    if domain == 'chip_glass_thermomechanical_family_v1':
        from glass_presentation import build_glass_markdown
        return build_glass_markdown(report, sources)
    component='；'.join(str(report.get('scope',{}).get(k,'')) for k in ('application','component') if report.get('scope',{}).get(k))
    intro=f'针对{component or name+"配方设计"}，在下述材料状态与评价条件下，比较组成变化对目标性质的影响并给出优选配方。'
    parts=[HEADINGS[0],intro,table(['目标性能','本次优化要求'],[
        (PROPERTIES.get(o['property'],(o['property'],))[0],
         ('尽量接近'+number(o['target'])+' '+o['unit']) if o['mode']=='target' else '提高' if o['mode']=='max' else '降低') for o in goals])]
    grouped={}
    for key,source in sources.items():
        if source=='配方设计输入':continue
        grouped.setdefault(source,[]).append(PROPERTIES.get(key,(key,))[0])
    introduction = parts
    parts = [HEADINGS[2],'#### 3.1 性能预测模型及其计算内容',
              table(['采用的模型','预测性能'],[(source,'、'.join(labels)) for source,labels in grouped.items()]) if grouped else '请先确认可计算的输入范围。']
    if '统一合金 Transformer v10' in sources.values():
        parts += ['成分表示经材料Transformer计算后，与组成机理描述符、温度、材料类别和工艺条件融合，输出屈服强度与抗拉强度。本次调用已经训练的固定权重。',
          equation(r'\widehat{\mathbf y}=f_\theta(\mathbf c,\mathbf d,T,\mathbf p),\qquad \widehat{UTS}=\widehat{YS}+s_d\operatorname{softplus}(b)'),
          r'其中，$\mathbf c$是组成，$\mathbf d$是组成派生描述符，$T$是评价温度，$\mathbf p$是工艺条件；$s_d>0$是材料域保存的强度尺度，$b$是模型输出。该参数化保持抗拉强度不低于屈服强度。',
          '#### 3.2 组成机理描述符',
          '合金数值特征包括元素物性的组成加权统计、混合熵、尺寸差异、价电子数与温度代理量；它们与元素组成表示共同进入预测模型。',
          equation(r'x_i=\frac{c_i/M_i}{\sum_jc_j/M_j},\quad\bar p=\sum_ix_ip_i,\quad S_{\rm mix}/R=-\sum_ix_i\ln x_i'),
          r'$c_i$为质量百分比，$M_i$为原子量，$x_i$为原子分数，$p_i$为保存的元素物性，$R$为气体常数；原子百分比输入直接归一化为$x_i$。']
    elif domain=='ni_superalloy_hot_end' and sources:
        parts += ['镍基专项模型根据元素含量、制造路线、分段热处理及评价温度计算短时强度；蠕变模型另结合施加应力计算断裂寿命，延伸率由独立专项模型提供。',
          equation(r'(\widehat{YS},\widehat{UTS})=f_s(\mathbf c,r,h,T),\qquad \log_{10}\widehat t_r=f_r(\mathbf c,r,h,T,\sigma)'),
          r'$\mathbf c$为元素质量百分比，$r$为制造路线，$h$为分段热处理，$T$为评价温度，$\sigma$为蠕变试验应力，$t_r$以小时计。']
    elif domain=='copper_hot_end_local_composition_v1' and sources:
        parts += ['铜基专项模型以元素质量百分比、加工状态编码和评价温度预测短时抗拉与屈服强度。参考配方用于限定局部成分变化，预测采用本次指定的状态与温度。',
          equation(r'(\widehat{YS},\widehat{UTS})=f_{\rm Cu}(\mathbf c,\mathbf p,T)'),
          r'$\mathbf c$为元素质量百分比，$\mathbf p$为加工状态特征，$T$为评价温度。']
    elif domain=='chip_glass_thermomechanical_family_v1' and sources:
        parts += ['玻璃Transformer预测密度和模量，CTE与其余工艺性质由相应专项模型计算。输入保持氧化物摩尔组成及各性质的评价条件。',
          equation(r'q_i=c_i/100,\qquad \widehat y_t=f_\theta(\mathbf q,\mathbf d,t,T,m_T)'),
          r'$c_i$为氧化物摩尔百分比，$q_i$为组分分数，$t$是性质任务，$T,m_T$是温度与其可用性标记。',
          '#### 3.2 组成结构描述符',
          '玻璃Transformer使用组分数、混合熵、最大组分分数，以及网络形成体、中间体、碱金属和碱土金属氧化物分数等21个组成特征。',
          equation(r'S_{\rm mix}/R=-\sum_iq_i\ln q_i,\qquad f_G=\sum_{i\in G}q_i'),
          r'$G$是指定氧化物类别，$f_G$是该类的组成分数；这些量是组成结构代理描述符。']
    elif domain=='short_cf_thermomechanical_rve_v1' and sources:
        parts += ['专项模型以基体弹性参数、碳纤维体积分数、有效长度和主方向取向为输入，输出等效正交各向异性工程常数。',
          '#### 3.2 设计变量与本构约束',
          equation(r'\boldsymbol\sigma=\mathbf C\boldsymbol\varepsilon,\qquad V_f+V_m=1'),
          r'$\mathbf C$为等效刚度矩阵，$V_f,V_m$分别是纤维和基体体积分数；完整本构检查刚度矩阵正定性与泊松比互易关系。']
    elif domain=='perovskite_transport_stability_v2' and sources:
        parts += ['输运模型依据组分和温度预测变温电输运响应，并计算分温区有效活化能。',
          equation(r'y(T)=\ln[\sigma(T)T],\qquad E_a=-k_B\frac{\mathrm d y}{\mathrm d(1/T)}'),
          r'$\sigma$是沿原始数据单位定义的电导率，$T$为绝对温度，$k_B$为玻尔兹曼常数。']
    parts += ['#### 3.3 配方比较与优化方法']
    if report['method']=='nsga2':
        parts += ['NSGA-II在给定范围内迭代调整配方，通过选择、交叉、变异及非支配排序比较目标间的取舍。本次材料处理状态与评价条件保持固定。',
          equation(r'\min_{\mathbf c}\mathbf F(\mathbf c),\qquad l_i\le c_i\le u_i')]
        if domain!='short_cf_thermomechanical_rve_v1':parts.append(equation(r'\sum_i c_i=100'))
        parts += [r'$l_i,u_i$为组成或设计变量上下限。提高某性质时最小化其预测值的负数；接近目标值时最小化绝对差$|\widehat y-y^*|$。',
                  ('单目标搜索按目标值或与指定目标的差距推荐方案。' if len(goals)==1 else
                   '多目标搜索返回Pareto权衡方案。未另指定推荐偏好时，将各目标在当前非支配解中的差距归一化，再按平均差距推荐均衡方案；该步骤与NSGA-II的非支配搜索分开。')]
    elif report['method']=='documented_grid':
        parts += ['按当前文档化组分网格比较220 K和300 K输运响应，沿已有服务的目标规则保留优选方案。']
    else:
        parts += ['先核对请求范围与可用参考配方。当前范围未通过该项检查，本轮没有执行NSGA-II或输出新预测。']
    methods = parts
    parts = [HEADINGS[1], '#### 2.1 参数采用情况', parameter_table(report)]
    bounds=report.get('scope',{}).get('element_bounds_wt_percent') or report.get('scope',{}).get('element_bounds_at_pct') or report.get('scope',{}).get('oxide_bounds_mol_percent') or report.get('resolved_design',{}).get('bounds')
    if bounds:
        if domain=='short_cf_thermomechanical_rve_v1':
            labels={'target_vf':('碳纤维体积分数','%'),'fiber_length_mm':('有效纤维长度','mm'),'target_a11':('主方向取向参数 a₁₁','无量纲')}
            rows=[(labels[e][0],number(b[0]*(100 if e=='target_vf' else 1)),number(b[1]*(100 if e=='target_vf' else 1)),labels[e][1]) for e,b in bounds.items()]
        else:
            rows=[(NAMES.get(e,e)+'（'+e+'）',number(b[0]),number(b[1]),basis) for e,b in bounds.items()]
        parts += ['#### 2.2 材料配比与结构参数范围',table(['材料配比或结构变量','下限','上限','单位'] if domain=='short_cf_thermomechanical_rve_v1' else ['材料配比变量','含量下限','含量上限','单位'],rows)]
        parts.append('上下限相同表示该项固定。各组分按表中单位计量，并满足组成总量约束。')
    limits=report.get('scope',{}).get('screening_thresholds') or {}
    if limits:
        labels={'uts_min_MPa':('抗拉强度下限','MPa'),'proof_strength_min_MPa':('屈服强度下限','MPa'),
                'rupture_life_min_h':('蠕变断裂寿命下限','h'),'CTE_linear_0_to_300C_max_ppm_per_K':('平均线膨胀系数上限','ppm/K'),
                'density_g_per_cm3_max':('密度上限','g/cm³'),'young_modulus_GPa_min':('杨氏模量下限','GPa'),
                'stress_optical_coefficient_nm_cm_per_MPa_max':('应力光学系数上限','nm/(cm·MPa)')}
        parts += ['#### 2.3 必须满足的性能要求', table(['性能要求','本次设定'],[(labels.get(k,(k,''))[0],number(v)+' '+labels.get(k,(k,''))[1]) for k,v in limits.items()])]
    parts = introduction + parts + methods + [HEADINGS[3]]
    if not candidates:
        if report.get('input_issue')=='NO_ELIGIBLE_STARTING_REFERENCE':
            parts += ['当前请求范围内没有可用的优化起始参考配方。',
                      HEADINGS[4],'补充该范围内可用于优化的独立参考配方，或确认新的成分范围后再计算。']
        else:
            parts += ['当前约束下没有同时满足要求的配方。',HEADINGS[4],'保留原有要求，优先核对限制最强的组成、工艺或性能条件。']
        return '\n\n'.join(parts + ['### 6. 优化结论', conclusion])
    if nearest:
        parts += ['本次没有找到同时满足全部性能要求的配方。以下列出成分、工况与物理检查通过且性能缺口最小的方案，原要求保持不变。']
    elif report['method']=='nsga2':
        reason={'model_evaluation_budget':'达到设定的模型评估预算','proposal_budget':'达到候选提议预算',
                'soft_wall_budget':'达到设定的搜索时间','documented_grid':'完成组分网格比较'}.get(report.get('stop_reason'),'按已记录停机条件结束')
        parts += [f'计算已完成，{reason}。保留{len(report["front"])}个目标权衡方案，以下列出优先比较的配方。']
    else:parts += ['已完成文档化组分比较，以下列出优选配方及输运性质。']
    parts += ['#### 4.1 配方方案与目标性能']
    keys=[o['property'] for o in goals]
    headers=['配方方案']+[PROPERTIES[k][0]+'（'+PROPERTIES[k][1]+'）' for k in keys]
    parts += [table(headers,[[r['display_id']+('（优选配方）' if i==0 and not nearest else '')]+[property_value(k,r['values'][k])[1] for k in keys] for i,r in enumerate(candidates)]),
              '{{VISUAL:recipe_tradeoff}}',chart_caption(report),HEADINGS[4], '#### 5.1 '+preferred_label(report)+'的完整配比']
    top=candidates[0]
    if nearest:
        rows=[]
        for gap in top['constraint_gaps'].values():
            key=gap['property'];label,observed,unit=property_value(key,gap['observed'])
            target=property_value(key,gap['target'])[1]
            shortfall=property_value(key,gap['shortfall'])[1]
            rows.append((label,('≥ ' if gap['direction']=='min' else '≤ ')+target+' '+unit,
                         observed+' '+unit,'满足' if gap['shortfall']==0 else '差 '+shortfall+' '+unit))
        parts += [table(['要求性质','用户要求','模型预测','满足情况'],rows),
                  '接近方案按各项性能要求的相对缺口之和比较；它与全部达标方案分开记录。']
    else:
        parts += [('该配方按本次单目标的优化方向优先推荐。' if len(goals)==1 else
               '该配方在上述条件下提供较均衡的目标组合。')+'配方编号用于本次设计记录。']
    if domain=='perovskite_transport_stability_v2':
        parts.append('A位与卤素位分别归一化为100%；铅占据B位，化学计量保持ABX₃。')
    for index,(group,amounts,unit) in enumerate(component_groups(report,top)):
        if len(component_groups(report,top))>1:parts.append('#### '+group+'组成')
        parts += [table(['材料组分','含量（'+unit+'）'],[((NAMES[e]+'（'+e+'）' if e in NAMES and e not in {'fiber','matrix'} else NAMES.get(e,e)),number(v)) for e,v in sorted(amounts.items(),key=lambda r:(-r[1],r[0])) if v>0]),f'{{{{VISUAL:recipe_composition_{index}}}}}']
    if domain=='short_cf_thermomechanical_rve_v1':
        c=top['composition']['values'];parts.append(table(['结构设计参数','本次采用'],[
            ('有效纤维长度',number(c['fiber_length_mm'])+' mm'),('主方向取向参数 a₁₁',number(c['target_a11']))]))
    parts += ['#### 5.2 '+preferred_label(report)+'的完整预测性能',table(['预测性能','预测值','如何理解'],[
        (property_value(k,v)[0],property_value(k,v)[1]+' '+property_value(k,v)[2],property_meaning(k)) for k,v in top['values'].items()])]
    parts += ['#### 5.3 配方验证建议',
              (f'针对{component or name+"配方设计"}，在上述条件下，{top["display_id"]}是当前计算中接近目标的配方；按上表未满足的性质继续优化，并保留其余已满足条件。' if nearest else
               f'针对{component or name+"配方设计"}，在上述条件下优先评估{top["display_id"]}，其组成与预测性质见上表。下一步按相同材料状态开展验证，再确定制备制度。')]
    return '\n\n'.join(parts + ['### 6. 优化结论', conclusion])


def validate_markdown(text):
    if text.count('$$')%2 or any(term in text for term in ('\\[','NaN','nan MPa','占位','待生成','IMG_TOKEN')):
        raise ValueError('invalid formula or non-final report text')
    headings = ('### 1. 问题描述','### 2. 使用模型与数学定义','### 3. 本次参数、条件与可调整项','### 4. 输出定义及验证路径','### 5. 筛选结果与候选卡')
    if '### 1. 设计需求与优化目标' in text:
        from report_outline import HEADINGS
        headings = HEADINGS
    elif '### 1. 玻璃配方设计需求与优化目标' in text:
        from report_outline import LEGACY_GLASS_HEADINGS
        headings = LEGACY_GLASS_HEADINGS
    for h in headings:
        if h not in text:raise ValueError('heading contract violation')
    for label in ('种群规模','候选提议上限','随机种子','外层调用硬超时','搜索软时间预算'):
        if label in text:raise ValueError('implementation parameter leaked into report')
    for i,line in enumerate(text.splitlines()):
        if line.startswith('#') and i+1<len(text.splitlines()) and text.splitlines()[i+1].strip():
            raise ValueError('heading needs a blank line')


def embed_assets(markdown, assets, *, public=False):
    mapping={a['name']:a for a in assets}
    def replace(match):
        name=match.group(1)
        if name not in mapping:raise ValueError('missing visualization asset: '+name)
        asset=mapping[name];url=str(asset['url'])
        if public and not url.startswith('https://'):raise ValueError('public images require existing HTTPS asset publisher')
        return f'![{asset["title"]}]({url})'
    result=re.sub(r'\{\{VISUAL:([a-z0-9_]+)\}\}',replace,markdown)
    if '{{VISUAL:' in result:raise ValueError('unresolved visualization token')
    validate_markdown(result)
    return result


def render_private(report, directory):
    """Write only within the caller's owned shadow directory; no upload."""
    private_flags(report)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    from recipe_plots import render_assets
    assets=render_assets(report,directory)
    markdown=build_markdown(report);validate_markdown(markdown)
    (directory/'summary.md').write_text(markdown,encoding='utf-8')
    (directory/'preview.md').write_text(embed_assets(markdown,assets),encoding='utf-8')
    # Audit settings remain machine-readable, outside the main user report.
    audit={'display_version':VERSION,'domain':report['domain'],'method':report['method'],
           'nearest_count':len(report.get('nearest_candidates',[])),
           'stop_reason':report.get('stop_reason'),'counters':report.get('counters'),
           'settings':report.get('technical_settings'), 'model_metadata':report.get('model_metadata'),
           'elapsed_seconds':report.get('elapsed_seconds'),'public_effect':False,
           'display_allowed':False,'production_release_authorized':False,'formal_result_mutated':False,
           'assets':[{k:v for k,v in a.items() if k!='url'} for a in assets]}
    for asset in audit['assets']:
        path=directory/asset['filename'];asset['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    (directory/'display_manifest.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return audit
