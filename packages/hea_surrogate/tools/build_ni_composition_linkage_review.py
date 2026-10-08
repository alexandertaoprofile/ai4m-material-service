#!/usr/bin/env python3
"""Join review-only composition candidates to reviewed strength evidence.

This produces a reviewer packet, not a training file.  It makes every proposed
composition-to-strength linkage explicit before the registry can be expanded.
"""
from __future__ import annotations
import argparse, csv, json
from collections import Counter, defaultdict
from pathlib import Path


def read(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(path, fields, records):
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        out=csv.DictWriter(f, fieldnames=fields); out.writeheader(); out.writerows(records)


def numeric(value):
    try: float(value); return True
    except (ValueError, TypeError): return False


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--composition-candidates', required=True)
    ap.add_argument('--strength-evidence', required=True)
    ap.add_argument('--output-dir', required=True, type=Path)
    a=ap.parse_args()
    if a.output_dir.exists(): raise SystemExit(f"refusing to overwrite existing output: {a.output_dir}")
    components=read(a.composition_candidates)
    strength=[r for r in read(a.strength_evidence) if r['property'] in {'yield_strength','ultimate_tensile_strength'}]
    component_by_id=defaultdict(list)
    for row in components:
        if row['candidate_material_id']: component_by_id[row['candidate_material_id']].append(row)
    linked=[]
    for material_id, rows in sorted(component_by_id.items()):
        evidence=[r for r in strength if r['material_id']==material_id]
        observed=[r for r in evidence if numeric(r['value']) and numeric(r['temperature_K'])]
        linked.append({
            'material_id':material_id, 'display_name':rows[0]['candidate_display_name'],
            'source_composition_tables':';'.join(sorted({r['source_table_id'] for r in rows})),
            'component_rows':len(rows), 'distinct_elements':';'.join(sorted({r['element'] for r in rows})),
            'reviewed_strength_points_total':len(evidence),
            'reviewed_strength_points_numeric_temperature':len(observed),
            'properties':';'.join(sorted({r['property'] for r in evidence})),
            'link_status':'review_required_before_training_admission',
            'review_action':'Confirm table scope, whether partial typical composition is acceptable for the target model, and link heat-treatment/product state without inference.',
        })
    a.output_dir.mkdir(parents=True)
    write(a.output_dir/'candidate_composition_strength_linkage.csv', list(linked[0]) if linked else [], linked)
    report={
        'review_only_composition_linkages':len(linked),
        'candidate_material_ids':[r['material_id'] for r in linked],
        'numeric_temperature_strength_points_if_all_candidates_approved':sum(int(r['reviewed_strength_points_numeric_temperature']) for r in linked),
        'admitted_training_rows':0,
        'reason':'The current registry excludes these composition tables; no training record is created until a human approves each table scope and composition completeness.',
    }
    (a.output_dir/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 既有镍基成分—强度链接复核包','', '下表把已有的显式成分候选与已复核强度证据并列展示。它是注册表扩展的逐项复核清单，不是训练集。','', '| 牌号 | 成分元素数 | 已复核强度点 | 带数值温度强度点 | 处理 |','|---|---:|---:|---:|---|']
    for r in linked:
        lines.append(f"| {r['display_name']} | {r['component_rows']} | {r['reviewed_strength_points_total']} | {r['reviewed_strength_points_numeric_temperature']} | 保持 review-only，确认表范围、成分完整性和状态关联后再准入 |")
    lines += ['', '即使这些候选全部获准，组成覆盖仍偏向耐腐蚀 Ni-Cr-Mo-W 合金，不能替代面向发动机热端的 γ/γ′ 析出强化超合金训练数据；外部公开超合金数据仍需引入并独立审计。']
    (a.output_dir/'composition_strength_linkage_review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
