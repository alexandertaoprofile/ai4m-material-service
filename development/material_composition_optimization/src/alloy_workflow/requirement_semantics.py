"""Material identity and numerical requirements, separate from model defaults.

No model invocation. Evidence context supplies identity/conditions, not commands.
"""
from copy import deepcopy
import math
import re

DOMAINS = {
 'hea': 'hea_mpea', 'nickel': 'ni_superalloy_hot_end',
 'steel': 'reusable_rocket_stainless', 'copper': 'copper_hot_end_local_composition_v1',
 'aluminum': 'aerospace_aluminum_composition_v1', 'glass': 'chip_glass_thermomechanical_family_v1',
 'cf': 'short_cf_thermomechanical_rve_v1', 'perovskite': 'perovskite_transport_stability_v2',
}
PATTERNS = {
 'hea': r'高熵|多主元|\bHEA\b|\bMPEA\b',
 'nickel': r'镍基|CMSX|Inconel|nickel[- ]?based|superalloy',
 'steel': r'奥氏体不锈钢|不锈钢|stainless|\b(?:304L?|316L?|301LN|30X)\b',
 'copper': r'铜基|铜合金|GRCop|NARloy|CuCrZr|Cu[-– ]Cr[-– ](?:Zr|Nb)',
 'aluminum': r'航空铝|铝合金|铝锂|Al[-– ]Li|alumin(?:um|ium)\s+alloy',
 'glass': r'玻璃基板|玻璃芯基板|封装(?:用)?玻璃|芯片玻璃|低硼无碱|铝硼硅酸盐玻璃|glass.?substrate|glass.?core',
 'cf': r'短碳纤维|短纤维增强热塑性|(?:PLA|PETG|ABS|ASA|PC)[- ]CF\b|short\s+carbon\s+fib(?:er|re)',
 'perovskite': r'钙钛矿|perovskite|Cs/FA/MA|Cs.*FA.*MA|碘溴',
 'titanium': r'钛合金|钛基|Ti[-–]6Al|Ti[-–]Nb',
 'high_strength_steel': r'高强钢|马氏体时效钢|18Ni|maraging|工具钢|马氏体不锈钢|双相不锈钢',
 'magnesium': r'镁合金|镁基',
 'electrolyte': r'电解质|electrolyte',
 'ceramic': r'钛酸钡|钛酸锶|氧化物钙钛矿|压电陶瓷|BaTiO3|SrTiO3',
}


def canonical_text(text):
    text = re.sub(r'(?<![A-Za-z])high[- ]entropy\s+alloy', '高熵合金', text, flags=re.I)
    text = re.sub(r'(?<![A-Za-z0-9])IN[- ]?718(?![A-Za-z0-9])', '镍基合金IN718', text, flags=re.I)
    text = re.sub(r'(?<![A-Za-z0-9])TC4(?![A-Za-z0-9])', '钛合金TC4', text, flags=re.I)
    text = re.sub(r'(?<![A-Za-z0-9])CuCrNb(?![A-Za-z0-9])', '铜基CuCrNb', text, flags=re.I)
    text = re.sub(r'(?<![A-Za-z0-9])((?:6061|7075)[-–]T\d+)(?![A-Za-z0-9])', r'铝合金\1', text, flags=re.I)
    text = re.sub(r'芯片封装(?:的|用的|用)玻璃', '芯片封装玻璃', text)
    text = re.sub(r'Cu\s*[-–]\s*Cr\s*[-–]\s*Zr', 'CuCrZr铜合金', text, flags=re.I)
    text = re.sub(r'Cu\s*[-–]\s*Cr\s*[-–]\s*Nb', '铜基Cu-Cr-Nb', text, flags=re.I)
    text = re.sub(r'(?<![A-Za-z])(PLA|PETG|ABS|ASA|PC)[- ]CF(?![A-Za-z])', r'短碳纤维增强\1', text, flags=re.I)
    text = re.sub(r'\bshort\s+carbon\s+fib(?:er|re)\s+reinforced\s+(PLA|PETG|ABS|ASA|PC)\b',
                  r'短碳纤维增强\1', text, flags=re.I)
    text = re.sub(r'芯片封装用玻璃', '芯片封装玻璃', text)
    text = re.sub(r'(?<![A-Za-z0-9])(304L?|316L?|301LN)(?![A-Za-z0-9])', r'奥氏体不锈钢\1', text, flags=re.I)
    # Require grade-language context for bare numbers; do not reinterpret units.
    text = re.sub(r'((?:给|针对|优化|调整|以)\s*)(6061)(?![\d.]|\s*(?:MPa|GPa|K|kg|g|°|℃))',
                  r'\1铝合金\2', text, flags=re.I)
    return text


def material_families(text):
    from src.alloy_workflow.contracts import _has_active_pattern
    # "nickel-base HEA" is one family; "nickel alloy and HEA" is two.
    text = re.sub(r'(?:镍基|铜基|钛基)(?=(?:的)?(?:高熵|多主元))', '', text)
    found = {key for key,p in PATTERNS.items() if _has_active_pattern(re.compile(p,re.I),text)}
    if 'ceramic' in found:found.discard('perovskite')
    if 'high_strength_steel' in found:found.discard('steel')
    if re.search(r'环氧|epoxy|连续(?:碳|玻璃)?纤维|长碳纤维|热固',text,re.I):
        if _has_active_pattern(re.compile(r'环氧|epoxy|连续(?:碳|玻璃)?纤维|长碳纤维|热固',re.I),text):
            found.discard('cf')
            # Composite descriptions can mention constituent material names.
            found.add('unsupported_composite')
    return found


def checked_domain(text, scope, decisions=None):
    from src.alloy_workflow.contracts import MaterialContextError
    found = material_families(text)
    if len(found)>1:
        for match in re.finditer(r'(?:优先(?:优化|考虑|采用)?|重点(?:优化|考虑)?|主要(?:优化|考虑)?|先(?:优化|做|用))\s*([^，,。；;\n]+)',text):
            if re.search(r'不|避免',text[max(0,match.start()-3):match.start()]):continue
            selected=material_families(match[1])
            if len(selected)==1 and selected<=found:
                found=selected
                if decisions is not None:
                    decisions.append({'field':'model_domain','parameter':'本次优化对象','adopted':match[1],
                        'reason':'按本轮明确的优先对象继续，其他材料保留为后续比较路线。'})
                break
    if len(found)>1:
        raise MaterialContextError('本次包含多个材料体系，请选择其中一种，或将各材料分别提交为配方优化任务。',text,[])
    requested=scope.get('model_domain')
    if found:
        family=next(iter(found)); domain=DOMAINS.get(family)
        if domain is None:
            if requested in DOMAINS.values():
                raise MaterialContextError('材料对象与指定模型路线不一致，请确认要优化的材料及模型范围。',text,[])
            raise ValueError('该材料体系尚未适配当前专项性能模型，转入材料配方与试验设计。')
        if requested and requested!=domain:
            conflicting_fields={'composition_basis','composition','composition_wt_percent','composition_mol_percent',
                'element_bounds_wt_percent','oxide_bounds_mol_percent','element_bounds_at_pct','processing','heat_treatment','matrix_name'} & scope.keys()
            if conflicting_fields:
                raise MaterialContextError('本次文字与附带的材料参数属于不同体系，先按原始要求整理分体系配方方案。',text,[])
            if decisions is not None:
                decisions.append({'field':'model_domain','parameter':'材料路线','adopted':'按本次文字明确的材料选择模型',
                    'reason':'附带的模型标签与本次材料名称不同，采用本次明确的材料对象。'})
        if family=='copper' and not requested and not re.search(r'配方|配比|成分(?:设计|优化|生成)|含量|组分|wt\.?%',text,re.I) and not any(k in scope for k in ('composition_wt_percent','element_bounds_wt_percent')):
            raise ValueError('已有铜合金牌号与证据查询请使用1105 成熟材料目录；局部配方优化可继续使用本服务。')
        return domain
    # Property words never establish a material identity on their own.
    return requested


NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
PROPERTY_PATTERNS = {
 'young_modulus_GPa':r'弹性模量|杨氏模量|(?<![A-Za-z0-9])E(?![A-Za-z0-9])',
 'CTE_linear_0_to_300C':r'(?:平均)?(?:线)?(?:热)?膨胀系数|(?<![A-Za-z0-9])CTE(?![A-Za-z0-9])',
 'density_g_per_cm3':r'密度',
 'stress_optical_coefficient_nm_cm_per_MPa':r'应力光学系数',
 'uts':r'抗拉强度|(?<![A-Za-z0-9])UTS(?![A-Za-z0-9])', 'yield_strength':r'(?:0[.．]2%\s*)?屈服强度|(?<![A-Za-z0-9])YS(?![A-Za-z0-9])',
 'elongation':r'延伸率|伸长率',
 'rupture_life':r'持久寿命|蠕变(?:断裂)?寿命',
}
UNITS={
 'young_modulus_GPa':'GPa', 'CTE_linear_0_to_300C':'ppm/K', 'density_g_per_cm3':'g/cm3',
 'stress_optical_coefficient_nm_cm_per_MPa':'nm/(cm MPa)',
 'uts':'MPa','yield_strength':'MPa','elongation':'%','rupture_life':'h',
}
THRESHOLDS={
 ('young_modulus_GPa','min'):'young_modulus_GPa_min',
 ('CTE_linear_0_to_300C','max'):'CTE_linear_0_to_300C_max_ppm_per_K',
 ('density_g_per_cm3','max'):'density_g_per_cm3_max',
 ('stress_optical_coefficient_nm_cm_per_MPa','max'):'stress_optical_coefficient_nm_cm_per_MPa_max',
 ('uts','min'):'uts_min_MPa',('yield_strength','min'):'proof_strength_min_MPa',
 ('rupture_life','min'):'rupture_life_min_h',
}


def parse_numeric_requirements(text, domain, supplied=None, decisions=None):
    """Only explicit test/goal language is operative; literature values stay facts."""
    from src.alloy_workflow.contracts import MaterialContextError
    changes={}; evidence={}; issues=[]; blockers=[]; supplied=supplied or {}
    decisions = decisions if decisions is not None else []
    from src.alloy_workflow.numeric_conditions import normalize_units, temperature_requirements, composition_ranges
    text=normalize_units(text)
    def assign(field,value,span):
        if field in changes and changes[field]!=value:
            decisions.append({'field':field,'parameter':'评价条件','adopted':span,
                'reason':'本次文字出现多个值，先采用最后明确的值。'})
        changes[field]=value;evidence[field]=span
    try:
        temperatures=temperature_requirements(text, decisions)
    except MaterialContextError as exc:
        temperatures=[];blockers.append(str(exc))
    for value, unit, span in temperatures:
        c=value-273.15 if unit.upper()=='K' else value
        if c < -273.15 or not math.isfinite(c):
            blockers.append('评价温度低于绝对零度或数值无效，原温度要求保留，需修正后计算。');continue
        if domain=='reusable_rocket_stainless':assign('test_temperature_K',round(c+273.15,8),span)
        elif domain in {'hea_mpea','ni_superalloy_hot_end','copper_hot_end_local_composition_v1','aerospace_aluminum_composition_v1'}:
            assign('test_temperature_C',round(c,8),span)
        elif domain=='perovskite_transport_stability_v2':assign('temperatures_K',[round(c+273.15,8)],span)
        elif domain=='chip_glass_thermomechanical_family_v1':issues.append('玻璃各项模型采用固定性质温区；请确认该温度用于哪项评价。')
    goals={}; gates={}; spans=[]
    cmp=r'至少|不低于|大于等于|达到|>=|≥|不超过|至多|不高于|小于等于|<=|≤|尽量接近|接近|目标(?:值)?(?:为|是)?|匹配|为|='
    if re.search(r'(?:导热率|热导率|比热容|疲劳寿命|压缩强度|T80|PCE)\s*(?:'+cmp+r')\s*'+NUMBER,text,re.I):
        issues.append('本次要求包含模型暂未支持的性质：'+ '；'.join(m[0] for m in re.finditer(r'(?:导热率|热导率|比热容|疲劳寿命|压缩强度|T80|PCE)\s*(?:'+cmp+r')\s*'+NUMBER+r'[^，。；\n]*',text,re.I)))
    units=r'GPa|MPa|ppm\s*/\s*K|(?:10\s*\^?\s*[-−]6|10⁻⁶)\s*/\s*K|g\s*/\s*cm[³3]|kg\s*/\s*m[³3]|%|h|小时|nm/\(cm[·*]MPa\)'
    for key,pattern in PROPERTY_PATTERNS.items():
        for m in re.finditer('(?:'+pattern+r')\s*(?:应|需|要|希望|应当|要求)?\s*('+cmp+r')\s*('+NUMBER+r')\s*('+units+r')?',text,re.I):
            operator,value,unit=m[1],float(m[2]),m[3]
            if re.search(r'不(?:要|必|用)|无需|避免',text[max(0,m.start()-4):m.start()]):
                changes.setdefault('_disabled_objectives',[]).append(key)
                issues.append('保留该项否定要求用于方案设计：'+m[0]);continue
            if not unit:
                if re.match(r'\s*[A-Za-z°/×]',text[m.end():]):
                    issues.append('尚未支持该项单位：'+text[m.start():m.end()+12]);continue
                unit=UNITS[key]
                decisions.append({'field':'objectives' if operator in {'尽量接近','接近','匹配'} or operator.startswith('目标') else 'screening_thresholds',
                    'parameter':'性能数值单位','adopted':m[0]+' '+unit,
                    'reason':'原文未标单位，本次暂采用该项性能的常用计算单位。'})
            unit=re.sub(r'\s','',unit)
            expected=UNITS[key]
            if expected=='GPa' and unit=='MPa':value/=1000
            elif expected=='MPa' and unit=='GPa':value*=1000
            elif expected=='g/cm3' and unit in {'kg/m³','kg/m3'}:value/=1000
            elif expected=='g/cm3' and unit in {'g/cm³','g/cm3'}:pass
            elif expected=='ppm/K' and unit in {'ppm/K','10^-6/K','10-6/K','10⁻⁶/K'}:pass
            elif expected=='h' and unit=='小时':pass
            elif key=='stress_optical_coefficient_nm_cm_per_MPa' and unit in {'nm/(cm·MPa)','nm/(cmMPa)'}:pass
            elif unit!=expected:
                issues.append(m[0]+'的单位与性质不一致');continue
            if not math.isfinite(value):
                issues.append('性能数值必须有限');continue
            glass_keys={'young_modulus_GPa','CTE_linear_0_to_300C','density_g_per_cm3','stress_optical_coefficient_nm_cm_per_MPa'}
            tensile_domains={'reusable_rocket_stainless','ni_superalloy_hot_end','copper_hot_end_local_composition_v1','aerospace_aluminum_composition_v1'}
            supported=(domain==DOMAINS['glass'] and key in glass_keys) or (domain in tensile_domains and key in {'yield_strength','uts','elongation'}) or (domain==DOMAINS['nickel'] and key=='rupture_life') or (domain==DOMAINS['hea'] and key in {'yield_strength','uts'})
            if not supported:
                issues.append('当前材料模型尚未接入该项数值目标：'+m[0]);continue
            if operator in {'至少','不低于','大于等于','达到','>=','≥'}:direction='min'
            elif operator in {'不超过','至多','不高于','小于等于','<=','≤'}:direction='max'
            else:direction='target'
            if direction=='target':
                goal={'goal':'target','target':value,'unit':expected}
                if key in goals and goals[key]!=goal:
                    decisions.append({'field':'objectives','parameter':'性能目标值','adopted':m[0],
                        'reason':'同一性能给出了多个目标，先采用最后明确的目标值。'})
                goals[key]=goal
            elif (key,direction) in THRESHOLDS:
                gate=THRESHOLDS[key,direction]
                if gate in gates and gates[gate]!=value:
                    value=max(gates[gate],value) if direction=='min' else min(gates[gate],value)
                    decisions.append({'field':'screening_thresholds','parameter':'重复性能门槛','adopted':str(value)+' '+expected,
                        'reason':'同方向门槛同时保留，采用两者中较严格的要求。'})
                gates[gate]=value
            else:issues.append('当前优化器尚未接入该方向的硬门槛：'+m[0])
            spans.append(m[0])
    # Matching is a target-distance task, not minimization of the coefficient.
    matching=bool(re.search(r'(?:匹配|接近)[^。；\n]{0,35}(?:热膨胀|线膨胀|CTE)|(?:热膨胀|线膨胀|CTE)[^。；\n]{0,25}(?:匹配|接近)',text,re.I))
    supplied_goals=supplied.get('objectives') or {}
    existing_cte=supplied_goals.get('CTE_linear_0_to_300C',supplied_goals.get('cte_0_300_ppm_K',{}))
    has_cte_target=isinstance(existing_cte,dict) and existing_cte.get('goal')=='target' and isinstance(existing_cte.get('target'),(int,float)) and math.isfinite(existing_cte['target'])
    if domain=='chip_glass_thermomechanical_family_v1' and matching and 'CTE_linear_0_to_300C' not in goals and not has_cte_target:
        m=re.search(r'(?:相邻材料|硅基板|目标材料)[^。；\n]{0,25}(?:CTE|(?:线|热)?膨胀系数)\s*(?:为|是|[:：=])?\s*('+NUMBER+r')\s*ppm\s*/\s*K',text,re.I)
        if m:goals['CTE_linear_0_to_300C']={'goal':'target','target':float(m[1]),'unit':'ppm/K'};spans.append(m[0])
        else:
            goals.setdefault('CTE_linear_0_to_300C',{'goal':'minimize'})
            goals.setdefault('young_modulus_GPa',{'goal':'maximize'})
            decisions.append({'field':'objectives','parameter':'热膨胀匹配目标','adopted':'先提供低膨胀与高刚度的起始配方',
                'reason':'尚无相邻材料在0–300°C的系数目标值；本次先比较可行配方，匹配误差留待目标值补充后计算。'})
    # Explicit qualitative goals are retained alongside a numerical target.
    # Never infer an unmentioned property goal from a literature result.
    if domain==DOMAINS['glass']:
        for key in ('CTE_linear_0_to_300C','young_modulus_GPa','density_g_per_cm3','stress_optical_coefficient_nm_cm_per_MPa'):
            pattern=PROPERTY_PATTERNS[key]
            for m in re.finditer(r'(降低|减小|提高|增大)\s*(?:'+pattern+')',text):
                if re.search(r'不(?:要)?|无需|避免',text[max(0,m.start()-3):m.start()]):
                    changes.setdefault('_disabled_objectives',[]).append(key)
                    issues.append('请明确该性能的目标值或允许范围：'+m[0]);continue
                goal={'goal':'minimize' if m[1] in {'降低','减小'} else 'maximize'}
                if key in goals and goals[key].get('goal')=='target':
                    decisions.append({'field':'objectives','parameter':'优化方向','adopted':'采用已明确的目标值',
                        'reason':'同一性能还提到定向变化，本次优先最小化与明确目标值的差距。'})
                    continue
                goals[key]=goal;spans.append(m[0])
    if gates:assign('screening_thresholds',gates,'；'.join(spans))
    if goals:assign('objectives',goals,'；'.join(spans))
    try:
        ranges=composition_ranges(text,domain,decisions)
    except MaterialContextError as exc:
        ranges={};blockers.append(str(exc))
    for key,value in ranges.items():assign(key,value,text)
    if issues:
        changes['_uncomputed_requirements']=issues
        for issue in issues:
            decisions.append({'field':'uncomputed_requirements','parameter':'尚未计算的要求',
                'adopted':'保留原要求，继续计算当前模型支持的性质','reason':issue})
    if blockers:
        changes['_calculation_blockers']=blockers
    return changes,evidence


def merge_requirements(inherited, parsed, explicit, text, decisions=None):
    from src.alloy_workflow.contracts import MaterialContextError
    decisions = decisions if decisions is not None else []
    aliases={'yield_strength_MPa':'yield_strength','yield_0p2_MPa':'yield_strength','proof_strength_0p2_MPa':'yield_strength','YS_MPa':'yield_strength',
             'uts_MPa':'uts','ultimate_tensile_strength_MPa':'uts','UTS_MPa':'uts',
             'cte_0_300_ppm_K':'CTE_linear_0_to_300C','density_g_cm3':'density_g_per_cm3',
             'rupture_hours':'rupture_life','elongation_pct':'elongation'}
    def normalized(layer):
        layer=deepcopy(layer)
        if isinstance(layer.get('objectives'),dict):
            goals={}
            for key,value in layer['objectives'].items():
                key=aliases.get(key,key)
                if key in goals and goals[key]!=value:
                    raise MaterialContextError('同一性能的不同字段给出了冲突目标，请确认。',text,[])
                goals[key]=value
            layer['objectives']=goals
        return layer
    inherited,parsed,explicit=map(normalized,(inherited,parsed,explicit))
    result=deepcopy(inherited)
    for layer in (explicit,parsed):
        for key,value in layer.items():
            if key in {'screening_thresholds','objectives','element_bounds_wt_percent','element_bounds_at_pct','oxide_bounds_mol_percent','candidate_windows'} and isinstance(value,dict):
                old=result.get(key,{})
                if not isinstance(old,dict):old={}
                if layer is parsed and any(k in old and old[k]!=v for k,v in value.items()):
                    decisions.append({'field':key,'parameter':'优化目标与性能门槛','adopted':'采用本次文字中的明确要求',
                        'reason':'与附带参数有差异，优先采用本次文字要求；其他不冲突的条件继续保留。'})
                result[key]={**old,**value}
            else:
                if layer is parsed and key in result and result[key]!=value:
                    decisions.append({'field':key,'parameter':'评价条件','adopted':str(value)+(' K' if key=='test_temperature_K' else '°C' if key=='test_temperature_C' else ''),
                        'reason':'采用本次文字中的明确条件，更新上文或附带参数。'})
                result[key]=deepcopy(value)
    return result
