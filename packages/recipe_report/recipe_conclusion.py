"""Plain-text conclusions from the same selected row used in report tables.

No LLM, model calls, new predictions or numerical conversions outside the
display's existing unit/precision formatters. Shared by page and agent return.
"""


def build_conclusion(report, display):
    if report.get('scoped_outcome'):
        from scoped_presentation import conclusion
        return conclusion(report,display)
    rows = report.get('candidates') or report.get('nearest_candidates') or []
    if not rows:
        return '本次没有形成可推荐的配方及对应预测性能，原配比范围与性能要求保持不变。'
    top = rows[0]
    nearest = not report.get('candidates')
    domain = report['domain']
    name = display.DOMAINS[domain][0]
    title = '接近目标配方' if nearest else '优选配方'
    parts = [('本次没有全部达标配方；' if nearest else '')
             + '本次优化的材料为' + name + '，' + title + '编号为' + top['display_id'] + '。']
    if (report.get('requested_scope') or report.get('scope') or {}).get('_uncomputed_requirements'):
        parts.append('本次按已支持的性质比较配方；另有要求暂不可计算，尚未判定全部用户要求均已满足。')
    for note in report.get('execution_notes',[]):
        parts.append(str(note['parameter'])+'：'+str(note['adopted'])+'。'+str(note['reason']))
    names = getattr(display, 'NAMES', {})
    basis_names = {'wt.%': '质量百分比', 'at.%': '原子百分比', 'mol.%': '摩尔百分比', 'vol.%': '体积百分比'}
    for group, amounts, unit in display.component_groups(report, top):
        values = []
        for key, value in sorted(amounts.items(), key=lambda item: (-item[1], item[0])):
            if value <= 0:
                continue
            label = names.get(key, key)
            if key in names and key not in {'fiber', 'matrix'}:
                label += '（' + key + '）'
            values.append(label + '为' + display.number(value) + '%')
        parts.append(group + '按' + basis_names.get(unit, unit) + '（' + unit + '）计：' + '；'.join(values) + '。')
    if domain == 'perovskite_transport_stability_v2':
        parts.append('A位与卤素位分别归一化为100%；B位为铅，化学计量为ABX₃。')
    if domain == 'short_cf_thermomechanical_rve_v1':
        c = top['composition']['values']
        parts.append('有效纤维长度为' + display.number(c['fiber_length_mm']) + ' mm，主方向取向参数a₁₁为'
                     + display.number(c['target_a11']) + '。')
    conditions = '；'.join(str(row[0]) + '为' + str(row[1]) for row in display.condition_rows(report))
    if conditions:
        parts.append('本次采用的评价条件：' + conditions + '。')
    predictions, design_inputs, derived = [], [], []
    for key, value in top['values'].items():
        label, amount, unit = display.property_value(key, value)
        item = label + '为' + amount + (' ' + unit if unit else '')
        source = top.get('property_sources', {}).get(key)
        if source == 'design_input' or key == 'fiber_volume_fraction':
            design_inputs.append(item)
        elif source == 'derived_E11_over_E22' or key == 'anisotropy_ratio_E11_E22':
            derived.append(item)
        else:
            predictions.append(item)
    if predictions:
        parts.append('在上述定义与条件下，模型预测结果为：' + '；'.join(predictions) + '。')
    if design_inputs:
        parts.append('配方设计输入：' + '；'.join(design_inputs) + '。')
    if derived:
        parts.append('由预测结果计算：' + '；'.join(derived) + '。')
    gaps = []
    for gap in top.get('constraint_gaps', {}).values():
        if gap['shortfall'] > 0:
            label, shortfall, unit = display.property_value(gap['property'], gap['shortfall'])
            target = display.property_value(gap['property'], gap['target'])[1]
            gaps.append(label + '要求' + ('≥' if gap['direction'] == 'min' else '≤')
                        + target + ' ' + unit + '，仍差' + shortfall + ' ' + unit)
    if gaps:
        parts.append('未满足项：' + '；'.join(gaps) + '。')
    if domain == 'chip_glass_thermomechanical_family_v1':
        parts.append('以上数值对应本次配方。泊松比、热导率和比热容未列入本次模型预测性能；后续仿真如采用估计值，应单独注明来源。')
    parts.append('以上性能用于本次配方比较，实际制备后的性能由相同条件下的试验核验。')
    return '\n\n'.join(parts)
