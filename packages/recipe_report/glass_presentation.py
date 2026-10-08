"""Glass-only public wording. No inference, ranking, input repair or publication."""
from recipe_display import NAMES, PROPERTIES, component_groups, condition_rows, equation, number, property_value, table
from report_outline import HEADINGS
from parameter_usage import parameter_table
from recipe_labels import preferred_label

DOMAIN = 'chip_glass_thermomechanical_family_v1'
SUBSCRIPTS = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
OXIDE_NAMES = dict(NAMES, Fe2O3='氧化铁')
PROPERTY_LABELS = {
    'cte_0_300_ppm_K': '平均线膨胀系数（0–300°C）',
    'young_modulus_GPa': '弹性模量（杨氏模量）',
}
MEANINGS = {
    'cte_0_300_ppm_K': '表示0–300°C温区内，每升高1°C产生的平均相对长度变化；数值越低，热膨胀越小。',
    'density_g_cm3': '表示单位体积的质量，可用于估算相同尺寸玻璃的重量。',
    'young_modulus_GPa': '表示抵抗弹性变形的能力；数值越大，材料的刚度越高。',
    'stress_optical_coefficient_nm_cm_per_MPa': '表示受力引起的光学变化程度，用于分析载荷对光学行为的影响。',
    'viscosity_temperature_200_poise_C': '表示达到200 poise指定黏度时的温度，用于比较高温流动特性。',
    'viscosity_temperature_35kpoise_C': '表示达到35,000 poise指定黏度时的温度，用于比较加工温度需求。',
}


def oxide_label(key):
    formula = key.translate(SUBSCRIPTS)
    name = OXIDE_NAMES.get(key)
    return name + '（' + formula + '）' if name else formula


def property_label(key):
    return PROPERTY_LABELS.get(key, PROPERTIES.get(key, (key,))[0])


def scheme_label(index):
    return '方案' + ('一', '二', '三', '四', '五')[index] if index < 5 else '方案' + str(index + 1)


def goal_text(goal):
    if goal['mode'] == 'target':
        return '尽量接近' + number(goal['target']) + ' ' + goal['unit']
    if goal['mode'] == 'min':
        return '在给定范围内尽量降低'
    if goal['mode'] == 'max':
        return '在给定范围内尽量提高'
    raise ValueError('unsupported glass objective mode')


def chart_caption(report):
    goals = report['objectives']
    nearest = not report['candidates'] and bool(report.get('nearest_candidates'))
    if len(goals) == 1:
        goal = goals[0]
        text = ('注：横轴对应上表中的配方编号，纵轴为' + property_label(goal['property'])
                + '的模型预测值。本次要求是' + goal_text(goal) + '。')
        if goal['mode'] == 'target':
            text += '虚线表示指定目标值，柱顶越接近虚线，越符合该目标。'
        if nearest:
            text += '尚未满足的性能要求见第5节。'
        return text
    text = ('注：每个点对应一个配方。横轴为' + property_label(goals[0]['property'])
            + '，纵轴为' + property_label(goals[1]['property']) + '。')
    text += '灰点表示通过输入检查的已计算配方；' if nearest else '灰点表示满足本次计算约束的已计算配方；'
    if report['front']:
        text += '彩色点表示目标之间各有取舍的配方。'
        if len(goals) > 2:
            text += '右侧色条对应' + property_label(goals[2]['property']) + '，按刻度读取数值。'
    text += '星形点表示' + preferred_label(report) + '。'
    text += '本次比较方向：' + '；'.join(property_label(g['property']) + goal_text(g) for g in goals) + '。'
    return text


def content_amount(value):
    # Preserve trace additions instead of rounding a positive amount to zero.
    if 0 < value < 0.000001:
        return format(value, '.6g')
    return number(value)


def build_glass_markdown(report, sources):
    candidates = report['candidates'] or report.get('nearest_candidates', [])
    nearest = not report['candidates'] and bool(candidates)
    goals = report['objectives']
    scope = report.get('scope', {})
    parts = [HEADINGS[0],
        '本次任务是在给定的氧化物含量范围内，为芯片封装玻璃基板设计配方。'
        '下表列出希望改善的性能；后文说明允许调整的组成、计算方法以及推荐方案。']
    if goals:
        parts.append(table(['目标性能', '本次优化要求', '性能含义'], [
            (property_label(g['property']), goal_text(g), MEANINGS.get(g['property'], '按本次指定目标比较。')) for g in goals]))
        cte_goal = next((g for g in goals if g['property']=='cte_0_300_ppm_K'), None)
        if cte_goal and cte_goal['mode']=='min':
            origin = report.get('provenance', {}).get('objectives')
            if any(n.get('parameter')=='热膨胀匹配目标' for n in report.get('execution_notes',[])):
                parts.append('本次先给出低膨胀与高刚度的起始配方。相邻材料的目标系数补充后，可按匹配差距更新推荐。')
            else:
                parts.append(('本次采用默认的低膨胀目标：' if origin in {None, 'platform_default', 'template_inference', 'default'} else '本次采用低膨胀目标：')
                             + '在允许的配比范围内降低平均线膨胀系数。若希望匹配相邻材料，可指定同一温区的系数目标值，按与该值的差距优化。')
        elif cte_goal and cte_goal['mode']=='target':
            parts.append('本次按平均线膨胀系数与指定值的差距优化，预测值越接近'
                         + number(cte_goal['target'])+' '+cte_goal['unit']+'，越符合该项要求。')
    else:
        parts.append('本次请求尚未进入性能优化，需先确认可计算的配比范围与目标。')

    parts += [HEADINGS[1], '#### 参数采用情况', parameter_table(report), '#### 2.1 组分含量与配比约束',
        '本次按下列组分含量范围调整配方。mol.%为摩尔百分比，即按物质的量计算各氧化物所占比例，各组分合计为100%。']
    bounds = scope.get('oxide_bounds_mol_percent') or report.get('resolved_design', {}).get('bounds')
    if bounds:
        parts.append(table(['玻璃组分', '含量下限（mol.%）', '含量上限（mol.%）'], [
            (oxide_label(key), content_amount(limits[0]), content_amount(limits[1])) for key, limits in bounds.items()]))
        parts.append('上下限相同表示该组分含量固定；上下限均为0表示本次设计不加入该组分。'
                     '如需调整范围，应重新核对模型适用范围后计算。')
    else:
        parts.append('本次记录的组分上下限尚不完整，调整配比前需补充确认。')
    parts += [equation(r'\sum_{i=1}^{n}c_i=100,\qquad l_i\leq c_i\leq u_i'),
        r'其中，$n$为参与配比的组分数，$c_i$为第$i$种组分的含量（本场景为mol.%），'
        r'$l_i$和$u_i$分别对应表中的含量下限和含量上限。公式表示：含量总和为100%，且每一组分均在允许范围内。']
    parts += ['#### 2.2 各项性能采用的评价条件', table(['评价项目', '本次采用的条件'], [
        ('平均线膨胀系数', '0–300°C温区的平均值。'),
        ('密度', '20°C条件下的密度。'),
        ('配方变化范围', '在本次参考玻璃配方及允许的氧化物含量范围内调整。'),
        ('弹性模量、应力光学系数与黏度特征温度', '采用对应模型的性质定义与评价条件。'),
    ])]
    temperature_rows = [row[:2] for row in condition_rows(report)
                        if row[0] in {'性能评价温度', '拉伸评价温度'}]
    if temperature_rows:
        parts.append(table(['请求中记录的条件', '本次设定'], temperature_rows))
        parts.append('各项性能的评价条件按上表执行，平均线膨胀系数采用0–300°C温区定义。')
    limits = scope.get('screening_thresholds') or {}
    if limits:
        labels = {
            'CTE_linear_0_to_300C_max_ppm_per_K': ('平均线膨胀系数上限', 'ppm/K'),
            'density_g_per_cm3_max': ('密度上限', 'g/cm³'),
            'young_modulus_GPa_min': ('弹性模量下限', 'GPa'),
            'stress_optical_coefficient_nm_cm_per_MPa_max': ('应力光学系数上限', 'nm/(cm·MPa)'),
        }
        parts += ['#### 2.3 必须满足的性能要求', table(['性能要求', '本次设定'], [
            (labels.get(k, (k, ''))[0], number(v) + ' ' + labels.get(k, (k, ''))[1]) for k, v in limits.items()])]

    parts += [HEADINGS[2], '#### 3.1 性能预测模型及其计算内容']
    grouped = {}
    for key, source in sources.items():
        grouped.setdefault(source, []).append(property_label(key))
    if grouped:
        parts.append(table(['采用的模型', '预测性能'], [(source, '、'.join(keys)) for source, keys in grouped.items()]))
        parts += ['#### 3.2 组成描述符与性能预测关系',
            '模型读取各氧化物的摩尔百分比。玻璃Transformer还使用由组成计算的特征，'
            '例如主要组分占比、组分分布，以及形成玻璃网络和改变网络结构的不同氧化物占比，'
            '用于描述组成与性能的关系。本次采用已训练的固定模型计算性能。']
        if '玻璃 Transformer strict v1' in grouped:
            parts += [equation(r'q_i=\frac{c_i}{100},\qquad \widehat y_t=f_{\theta}(\mathbf q,\mathbf d,t,T,m_T)'),
                r'该式概括玻璃Transformer的输入与输出：$q_i$是组分摩尔分数，$\mathbf q$为全部组分分数组成的向量，'
                r'$\mathbf d$是由组成计算的描述符，$t$指定要预测的性质，$T$为该性质输入中的温度，'
                r'$m_T$标记温度是否有记录；$\theta$为已训练的模型参数，$\widehat y_t$为换回实际单位后的预测值。'
                '各项性质按3.1节列出的模型分工计算。',
                equation(r'H_{\rm comp}=-\sum_{i:q_i>0}q_i\ln q_i,\qquad f_G=\sum_{i\in G}q_i'),
                r'$H_{\rm comp}$描述组成分布的分散程度，采用理想混合熵形式，是无量纲组成特征；'
                r'$G$表示指定的氧化物类别，$f_G$是这一类别的总摩尔分数。'
                '例如网络形成体和碱金属氧化物分别统计，作为模型的组成描述符。']
    else:
        parts.append('本次输入检查尚未进入性能预测，未生成新的性能数值。')
    parts += ['#### 3.3 优化目标的数学表达与求解方法']
    if report['method'] == 'nsga2':
        parts.append('采用NSGA-II优化算法，在上述含量范围内反复调整配方、调用模型计算性能并比较结果。'
                     '每次调整都需满足组成合计100%等计算约束。')
        parts += [equation(
            r'F_j(\mathbf c)=\begin{cases}'
            r'\widetilde y_j(\mathbf c)/s_j,&\mathrm{min}\\'
            r'-\widetilde y_j(\mathbf c)/s_j,&\mathrm{max}\\'
            r'|\widetilde y_j(\mathbf c)-y_j^{\ast}|/s_j,&\mathrm{target}'
            r'\end{cases}'),
            r'$j$表示第$j$个目标；min、max、target分别对应降低、提高、接近指定值。'
            r'$\widetilde y_j$是按优化器比较精度舍入后的预测值，$y_j^{\ast}$是用户指定的目标值，'
            r'$s_j>0$为该目标的数值尺度。三种要求均转换成“$F_j$越小越好”，尺度及舍入用于统一目标比较。',
            equation(r'\min_{\mathbf c\in\mathcal D}\mathbf F(\mathbf c),\qquad '
                     r'\mathbf F(\mathbf c)=\big(F_1(\mathbf c),\ldots,F_m(\mathbf c)\big)'),
            r'$m$为优化目标数量，$\mathcal D$表示同时满足配比边界、组成闭合、模型适用条件及已设性能要求的范围。'
            'NSGA-II结合模型预测，在该范围内迭代更新配方。']
        if len(goals) == 1:
            parts.append('本次为单目标优化：按该项性能的提高、降低或接近指定值的要求比较，优先推荐最符合目标的已计算方案。')
        else:
            parts.append('多个目标可能相互制约。对保留的每个方案，当前计算中不存在另一配方在所有目标上都不差、'
                         '且至少一项目标更好。这些方案称为Pareto方案，用于比较不同性能之间的取舍。')
            if not nearest:
                parts += ['#### 3.4 多目标方案的推荐排序',
                    '搜索与推荐排序是两个步骤。对已经获得的Pareto方案，先将各目标与当前最好结果的差距归一化，'
                    '再按平均差距从小到大排列：',
                    equation(r'J(\mathbf c)=\frac{1}{m}\sum_{j=1}^{m}r_j(\mathbf c),\qquad '
                        r'r_j(\mathbf c)=\begin{cases}'
                        r'\dfrac{F_j(\mathbf c)-F_j^{\min}}{F_j^{\max}-F_j^{\min}},'
                        r'&F_j^{\max}-F_j^{\min}>\varepsilon\\'
                        r'0,&\mathrm{otherwise}\end{cases}'),
                    r'$F_j^{\min}$和$F_j^{\max}$取自本次Pareto方案中第$j$个目标的最小值与最大值，'
                    r'$r_j$是该目标的相对差距，$\varepsilon=10^{-12}$用于识别没有有效变化的目标。'
                    r'$J$越小，按当前等权规则得到的综合差距越小；该分数用于本次配方的推荐排序。',
                    preferred_label(report)+'在本次计算的方案中具有较小的平均目标差距，兼顾各项目标表现。']
            else:
                parts.append('本次未找到全部达标的Pareto方案，因此接近要求的方案按相对性能缺口比较，'
                             '具体未满足项见第5节。')
    else:
        parts.append('本轮没有执行NSGA-II或输出新预测；需先解决输入范围或可用参考配方的问题。')

    parts += [HEADINGS[3]]
    if not candidates:
        if report.get('input_issue') == 'NO_ELIGIBLE_STARTING_REFERENCE':
            parts.append('当前请求范围内没有可用于优化的起始参考配方，本轮未执行配方优化。原有含量范围保持不变。')
        else:
            parts.append('当前约束下没有同时满足要求的配方，需进一步核对配比范围和性能要求。')
        parts += [HEADINGS[4], '本次没有可展示的推荐配方及预测性能。请先核对配比范围、性能要求或补充可用参考数据，再重新计算。']
        from recipe_conclusion import build_conclusion
        import recipe_display
        return '\n\n'.join(parts + ['### 6. 优化结论', build_conclusion(report, recipe_display)])
    if nearest:
        parts.append('本次没有同时满足全部性能要求的配方。下表是通过输入检查、但仍有性能差距的方案，'
                     '具体缺口见第5节。')
    else:
        evaluations = report.get('counters', {}).get('model_evaluations')
        prefix = '本次完成' + str(evaluations) + '次模型评估，' if evaluations is not None else '本次计算完成，'
        parts.append(prefix + '保留' + str(len(report['front'])) + '个目标权衡方案，以下展示优先比较的'
                     + str(len(candidates)) + '个方案。')
    parts.append('下表按本次目标的综合表现排列各配方。')
    keys = [g['property'] for g in goals]
    parts.append(table(['配方方案'] + [property_label(k) + '（' + PROPERTIES[k][1] + '）' for k in keys], [
        [row['display_id'] + ('（优选配方）' if i == 0 and not nearest else '')] +
        [property_value(k, row['values'][k])[1] for k in keys] for i, row in enumerate(candidates)]))
    parts += ['#### 4.1 不同配方的目标性能比较', '{{VISUAL:recipe_tradeoff}}', chart_caption(report), HEADINGS[4],
        ('以下列出'+preferred_label(report)+'的组成、预测性能及尚未满足的要求。' if nearest else
         '以下汇总'+preferred_label(report)+'的完整组成与预测性能。')]
    top = candidates[0]
    if nearest:
        rows = []
        for gap in top['constraint_gaps'].values():
            key = gap['property']
            _, observed, unit = property_value(key, gap['observed'])
            target = property_value(key, gap['target'])[1]
            shortfall = property_value(key, gap['shortfall'])[1]
            rows.append((property_label(key), ('≥ ' if gap['direction'] == 'min' else '≤ ') + target + ' ' + unit,
                         observed + ' ' + unit, '满足' if gap['shortfall'] == 0 else '仍差 ' + shortfall + ' ' + unit))
        parts += [table(['性能要求', '要求数值', '模型预测', '满足情况'], rows),
                  '接近目标配方按相对性能缺口之和排序。']
    parts += ['#### 5.1 ' + preferred_label(report) + '的完整配比']
    for index, (_, amounts, unit) in enumerate(component_groups(report, top)):
        parts += [table(['玻璃组分', '含量（' + unit + '）'], [
            (oxide_label(k), content_amount(v)) for k, v in sorted(amounts.items(), key=lambda item: (-item[1], item[0])) if v > 0]),
            '仅列出含量大于0的组分；未列出的设计组分在本方案中为0。显示数值经过舍入，计算仍使用完整精度。',
            '{{VISUAL:recipe_composition_' + str(index) + '}}',
            '组成图显示各组分的比例；小含量组分在图中合并为“其他组分”，但在上表及图例明细中逐项保留。']
    parts += ['#### 5.2 ' + preferred_label(report) + '的完整预测性能',
        table(['预测性能', '预测值', '如何理解'], [
            (property_label(k), property_value(k, v)[1] + ' ' + property_value(k, v)[2],
             MEANINGS.get(k, '按对应模型的数据定义理解。')) for k, v in top['values'].items()]),
        '#### 5.3 配方采用前需要验证的事项',
        '本页性能为模型预测，建议按'+preferred_label(report)+'试制并测量目标性能，重点核对与相邻封装材料的热膨胀匹配。'
        '实际投料时，将氧化物摩尔组成换算为原料称量配方，并结合黏度特征温度确定熔制与退火制度。'
        if not nearest else
        '先核对上表未满足的性能要求，再调整配比并重新计算；试制时测量目标性能及热膨胀匹配情况。']
    parts += [equation(r'\Delta\varepsilon_{\rm th}\approx(\alpha_g-\alpha_s)\Delta T'),
        r'该式说明热膨胀匹配的物理意义：$\alpha_g$与$\alpha_s$分别为玻璃和相邻材料在同一温区的平均线膨胀系数，'
        r'$\Delta T$为温度变化，$\Delta\varepsilon_{\rm th}$为两者自由热应变之差。'
        r'代入时需将ppm/K换算为K$^{-1}$（乘以$10^{-6}$）。'
        '该近似关系用于分析玻璃与相邻材料的热膨胀匹配。']
    from recipe_conclusion import build_conclusion
    import recipe_display
    return '\n\n'.join(parts + ['### 6. 优化结论', build_conclusion(report, recipe_display)])
