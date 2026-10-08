"""Shared visible IDs and concise explanations across all material routes."""
from recipe_display import PROPERTIES, property_value


def preferred_label(report):
    rows = report['candidates'] or report.get('nearest_candidates', [])
    label = '优选配方' if report['candidates'] else '接近目标配方'
    return label + ('（' + rows[0]['display_id'] + '）' if rows else '')


def chart_caption(report):
    goals = report['objectives']
    if not goals:
        return ''
    label = lambda g: PROPERTIES[g['property']][0]
    if len(goals) == 1:
        text = '注：横轴为配方编号，纵轴为' + label(goals[0]) + '的预测值。'
        if goals[0]['mode'] == 'target':
            text += '虚线表示目标值，越接近虚线越符合本次要求。'
    else:
        text = '注：每个点对应一个配方。横轴为' + label(goals[0]) + '，纵轴为' + label(goals[1]) + '。'
        if report['method'] == 'nsga2':
            text += '灰点为已评估配方，彩色点为目标之间各有取舍的Pareto方案。'
        else:
            text += '各点来自本次实际比较的已记录组分。'
        if len(goals) > 2 and report.get('front'):
            text += '右侧色条表示' + label(goals[2]) + '。'
        text += '星形标出' + preferred_label(report) + '。'
    directions = []
    for g in goals:
        direction = {'min': '尽量降低', 'max': '尽量提高'}.get(g['mode'])
        if g['mode'] == 'target':
            _, v, u = property_value(g['property'], g['target'])
            direction = '尽量接近' + v + ' ' + u
        directions.append(label(g) + direction)
    return text + '本次目标：' + '；'.join(directions) + '。'


def property_meaning(key):
    meanings = {
        'YS_MPa': '表示开始产生规定塑性变形时的应力，用于比较抗永久变形能力。',
        'UTS_MPa': '表示拉伸过程中达到的最大工程应力。',
        'elongation_pct': '表示拉断前的相对伸长程度，用于比较延性。',
        'rupture_hours': '在指定温度和应力下的预测断裂时间。',
        'hardness_HV': '表示抵抗局部压入变形的能力。',
        'phase_risk': '相分类模型给出的相关类别概率，用于比较组织倾向。',
        'fiber_volume_fraction': '碳纤维占复合材料总体积的比例。',
        'anisotropy_ratio_E11_E22': '主方向与横向刚度的比值，反映方向差异。',
        'ln_sigmaT_220K': '220 K时电导率与温度乘积的对数响应，沿用训练数据单位。',
        'ln_sigmaT_300K': '300 K时电导率与温度乘积的对数响应，沿用训练数据单位。',
        'Ea_low_eV': '低温区电输运响应的有效活化能。',
        'Ea_high_eV': '高温区电输运响应的有效活化能。',
    }
    if key.startswith('E') and key.endswith('_MPa'):return '沿标记方向抵抗弹性拉伸变形的能力。'
    if key.startswith('G') and key.endswith('_MPa'):return '在标记平面内抵抗弹性剪切变形的能力。'
    if key.startswith('V'):return '单向加载时横向应变与纵向应变的关联。'
    return meanings.get(key, '按第3节模型及本次评价条件解释。')
