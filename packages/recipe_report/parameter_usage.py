"""Customer-readable parameter audit. Display only; never changes inputs."""
from recipe_display import condition_rows, provenance, table, number

LABELS = {
    'application': '应用场景', 'component': '构件用途', 'weld_state': '焊接状态',
    'thickness_mm': '板厚（mm）', 'low_temperature_verification_K': '后续低温验证温度（K）',
    'verification_focus': '验证重点', 'ambient_process': '补充加工记录',
    'target_vf': '纤维体积分数搜索中心', 'fiber_length_mm': '有效纤维长度搜索中心（mm）',
    'target_a11': '主方向取向搜索中心', 'temperatures_K': '输运曲线评价温度（K）',
}


def readable(value):
    if isinstance(value, dict):
        return '；'.join(str(k) + '：' + readable(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return '、'.join(readable(v) for v in value)
    return str(value)


def usage_rows(report):
    rows = [(name, value, origin, '按此条件计算或比较性能')
            for name, value, origin in condition_rows(report)]
    scope = report.get('scope') or {}
    for key in ('element_bounds_wt_percent', 'element_bounds_at_pct', 'oxide_bounds_mol_percent'):
        if scope.get(key):
            rows.append(('组分含量范围', '见组分含量上下限表', provenance(report, key), '限制允许的配比范围'))
    if scope.get('screening_thresholds'):
        rows.append(('性能要求', '见必须满足的性能要求表', provenance(report, 'screening_thresholds'), '检查配方是否达标'))
    if scope.get('candidate_windows'):
        rows.append(('纤维含量与结构范围', '见材料配比与结构参数范围表', provenance(report,'candidate_windows'),
                     '按用户指定上下限搜索，模型适用条件另行检查'))
    if report.get('objectives'):
        rows.append(('优化目标', '见第1节目标表', provenance(report, 'objectives'),
                     '确定性能比较方向与推荐顺序'))
    requested = report.get('requested_scope') or {}
    for key, label in LABELS.items():
        value = scope.get(key, requested.get(key))
        if value in (None, '', [], {}):
            continue
        role = '用于应用说明与后续验证'
        if key in {'target_vf', 'fiber_length_mm', 'target_a11'}:
            role = '确定局部搜索范围；最终值见优选配方'
        elif key == 'temperatures_K':
            role = '计算输运曲线；方案排序采用220 K与300 K响应'
        display_value=number(float(value)*100)+' vol.%' if key=='target_vf' else readable(value)
        rows.append((label, display_value, provenance(report, key), role))
    if requested.get('num_candidates') is not None and report.get('method') == 'nsga2':
        rows.append(('请求的候选数量', readable(requested['num_candidates']), provenance(report, 'num_candidates'),
                     '用于初始请求；本页按优化结果展示最多5个方案'))
    return rows


def parameter_table(report):
    text = table(['参数', '本次采用值', '来源', '本次计算中的作用'], usage_rows(report))
    notes = report.get('execution_notes') or []
    if notes:
        text += '\n\n本次已按以下条件继续计算，后续可直接补充信息更新方案。\n\n'
        text += table(['事项','本次采用','采用依据'],[(n['parameter'],n['adopted'],n['reason']) for n in notes])
    return text
