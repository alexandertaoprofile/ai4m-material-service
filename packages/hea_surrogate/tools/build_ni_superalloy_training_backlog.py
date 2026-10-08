#!/usr/bin/env python3
"""Create the complete, model-lane-specific Ni-superalloy training backlog."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

CORE = {
    "MAT-IN718": "core_precipitation_strengthened",
    "MAT-1101-HT-IN725": "core_precipitation_strengthened",
    "MAT-1101-HT-INX750": "core_precipitation_strengthened",
    "MAT-1101-HT-HR41": "core_precipitation_strengthened",
    "MAT-1101-HT-H282": "core_gamma_prime_strengthened",
    "MAT-1101-HT-H263": "core_gamma_prime_strengthened",
}

def read(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write(path,fields,rows):
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--coverage',required=True);p.add_argument('--pdf-audit',required=True)
    p.add_argument('--training-seed',required=True);p.add_argument('--raw-audit',required=True)
    p.add_argument('--existing-composition-candidates',required=True)
    p.add_argument('--external-status',required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    if a.output_dir.exists():raise SystemExit(f'refusing to overwrite existing output: {a.output_dir}')
    coverage=read(a.coverage); audit={r['material_id']:r for r in read(a.pdf_audit)}
    seed=read(a.training_seed); seed_ids={r['material_id'] for r in seed}
    existing_ids={r['candidate_material_id'] for r in read(a.existing_composition_candidates) if r['candidate_material_id']}
    backlog=[]
    for r in coverage:
        material_id=r['material_id']; pdf=audit.get(material_id,{})
        role=CORE.get(material_id,'supplementary_or_boundary')
        if material_id in seed_ids:
            comp='admitted_structured_seed'; next_step='Keep as pipeline control; do not use alone for model fitting.'
        elif pdf.get('status')=='review_ready':
            comp='pdf_composition_review_ready'; next_step='Review rendered manufacturer composition page; approve range/balance encoding into training-only composition package.'
        elif material_id in existing_ids:
            comp='existing_raw_composition_review_ready'; next_step='Review the explicit source-table composition candidate and approve range/balance encoding into training-only composition package.'
        else:
            comp='composition_source_not_yet_extracted'; next_step='Manually inspect source PDF composition page or acquire an authoritative specification.'
        strength=int(r['strength_points_numeric_temperature'])
        if strength==0:
            strength_next='Retain the strength evidence, but add a numeric test-temperature field before short-time tensile training.'
        else:
            strength_next='Condition and temperature evidence present; await composition admission.'
        backlog.append({'material_id':material_id,'display_name':r['display_name'],'training_role':role,
            'composition_status':comp,'pdf_composition_page':pdf.get('composition_pages_found',''),
            'pdf_component_candidates':pdf.get('component_candidates','0'),'numeric_temperature_strength_points':strength,
            'short_time_tensile_action':next_step+' '+strength_next,
            'creep_rupture_action':'Find or parse creep/rupture rows only with stress, temperature, time, failure definition and heat-treatment state.',
            'calphad_action':'After composition approval, add this grade to thermodynamic-database calibration set; compare phase and solidus/liquidus evidence where available.',
            'training_admission':'seed_only' if material_id in seed_ids else 'review_required'})
    raw=read(a.raw_audit)
    lane_counts={}
    for lane,cats in {'creep_rupture_v1':{'creep','rupture'},'short_time_tensile_v0':{'tensile','mechanical'},'calphad_validation':{'phase','chemical_composition'}}.items():
        lane_counts[lane]=sum(int(r['row_count']) for r in raw if r['category'] in cats and r['disposition']!='exclude_welding_or_join')
    external=json.loads(Path(a.external_status).read_text())
    a.output_dir.mkdir(parents=True)
    write(a.output_dir/'complete_training_backlog_by_grade.csv',list(backlog[0]),backlog)
    external_rows=[{'source':'Cambridge Superalloy database','purpose':'multi-composition strength/heat-treatment/creep supplement','status':external['status'],'next_action':external['next_action'],'admission':'not_imported'}]
    write(a.output_dir/'external_training_source_status.csv',list(external_rows[0]),external_rows)
    summary={'core_engine_hot_end_grades':sum(1 for r in backlog if r['training_role'].startswith('core')),
        'core_grades_with_pdf_composition_review_ready':sum(1 for r in backlog if r['training_role'].startswith('core') and r['composition_status']=='pdf_composition_review_ready'),
        'core_numeric_temperature_strength_points_after_composition_review':sum(int(r['numeric_temperature_strength_points']) for r in backlog if r['training_role'].startswith('core')),
        'all_ni_grades_with_pdf_composition_review_ready':sum(1 for r in backlog if r['composition_status']=='pdf_composition_review_ready'),
        'all_ni_grades_needing_manual_composition_source':sum(1 for r in backlog if r['composition_status']=='composition_source_not_yet_extracted'),
        'raw_rows_by_specialised_lane':lane_counts,
        'external_data_status':external['status'],
        'training_start_condition':'After review/admission of exact PDF composition pages, construct a training-only feature set. Do not fit a generalisation model until enough independent grades are available for leave-grade-out validation.'}
    (a.output_dir/'training_backlog_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    md=['# 镍基高温合金训练候选与待补齐总表','',f"发动机热端核心池共 {summary['core_engine_hot_end_grades']} 个牌号；其中 {summary['core_grades_with_pdf_composition_review_ready']} 个已在本地厂商 PDF 找到直接成分页。核心池当前可关联的数值温度强度证据共 {summary['core_numeric_temperature_strength_points_after_composition_review']} 条，仍需完成成分页复核和训练专用准入。",'', '所有条目的逐项动作见 `complete_training_backlog_by_grade.csv`。短时强度、蠕变/持久和 CALPHAD 各自独立建模，不共享标签。']
    (a.output_dir/'training_backlog.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
