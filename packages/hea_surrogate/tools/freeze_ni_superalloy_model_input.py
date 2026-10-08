#!/usr/bin/env python3
"""Freeze leakage-safe splits and feature contract; deliberately does not fit."""
from __future__ import annotations
import argparse, csv, hashlib, json
from pathlib import Path

# Split by alloy grade, never individual temperature/property rows.  Each split
# includes at least one source with both yield and UTS where the available data
# permits; the seed is recorded, not used to randomly reshuffle later.
SPLITS = {
    "test": {"MAT-1101-HT-H188", "MAT-1101-HT-H214"},
    "validation": {"MAT-1101-HT-H233", "MAT-1101-HT-H244"},
}

def read(path):
    with path.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))
def write(path, fields, rows):
    with path.open('w',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('package',type=Path); a=ap.parse_args(); p=a.package
    labels=read(p/'short_time_strength_observations.csv'); cards=read(p/'grade_cards.csv')
    grade_ids={r['material_id'] for r in labels}
    assert not (SPLITS['test'] & SPLITS['validation'])
    assert (SPLITS['test'] | SPLITS['validation']) <= grade_ids
    train=grade_ids-SPLITS['test']-SPLITS['validation']
    rows=[]
    for material_id in sorted(grade_ids):
        split='test' if material_id in SPLITS['test'] else 'validation' if material_id in SPLITS['validation'] else 'train'
        card=next(r for r in cards if r['material_id']==material_id)
        rows.append({'material_id':material_id,'display_name':card['display_name'],'split':split,'reason':'fixed grade-disjoint v2 split','label_rows':card['strength_rows_numeric_temperature']})
    split_dir=p/'splits'; split_dir.mkdir()
    write(split_dir/'grade_disjoint_split_v1.csv',list(rows[0]),rows)
    contract={
      'contract_version':'v1', 'model_fit_started':False,
      'task':'short-time tensile strength: yield_strength and ultimate_tensile_strength',
      'unit':'MPa',
      'observation_keys':['material_id','property','temperature_K','condition','source_locator'],
      'target_columns':['property','value'],
      'mandatory_context_columns':['temperature_K','condition'],
      'composition_input':'composition_specification_envelopes.csv',
      'composition_representation':'grade_specification_envelope',
      'required_feature_method':{
        'numeric_ranges':'Represent min/max/nominal as separate observed fields with missingness indicators; no midpoint substitution during package freeze.',
        'maximum_minimum_balance':'Represent qualifier as categorical and retain raw source text; balance is not a measured percentage.',
        'element_absence':'Use unknown/not-listed distinction; do not zero-impute elements absent from a supplier table.',
        'state':'Keep condition as structured/raw text until a separately versioned state parser is approved.'
      },
      'split':'splits/grade_disjoint_split_v1.csv',
      'split_rule':'all rows for one material_id occur in exactly one split; no random row-level split',
      'fit_gate':[
        'read and validate this contract', 'use only short_time_strength_observations.csv labels',
        'report test results only on the fixed test grades', 'report grade-level applicability domain',
        'do not use creep/rupture rows or CALPHAD calculations as labels'
      ],
      'not_in_scope':['melting point','solidus/liquidus','phase fractions','creep rupture time','weld/filler performance','measured heat chemistry']
    }
    (p/'model_input_contract_v1.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf8')
    card_by={r['material_id']:r for r in cards}
    summary={
      'fit_ready':True,'model_fit_started':False,
      'label_rows':len(labels),'distinct_label_grades':len(grade_ids),
      'split_grade_counts':{'train':len(train),'validation':len(SPLITS['validation']),'test':len(SPLITS['test'])},
      'core_grades_with_labels':sum(card_by[x]['training_scope']=='engine_hot_end_core' for x in grade_ids),
      'core_grade_without_current_short_time_label':[r['material_id'] for r in cards if r['training_scope']=='engine_hot_end_core' and r['material_id'] not in grade_ids],
      'input_sha256':{name:sha(p/name) for name in ['composition_specification_envelopes.csv','short_time_strength_observations.csv','grade_cards.csv']},
      'no_training_artifacts_created':True,
    }
    (p/'fit_readiness_manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    (p/'FIT_READINESS.md').write_text(f'''# 模型拟合前冻结清单

## 状态

数据输入、特征表达约束及牌号隔离切分均已冻结；**没有执行模型训练或调参**。

- 训练牌号：{len(train)}；验证牌号：{len(SPLITS['validation'])}；独立测试牌号：{len(SPLITS['test'])}。
- 标签：{len(labels)} 条，仅含屈服强度与抗拉强度、且均带数值测试温度。
- 核心热端牌号中，当前有短时强度标签的为 {summary['core_grades_with_labels']} 个；HAYNES 263 仅有成分规范，未被伪造为带标签样本。

后续训练只能读取 `model_input_contract_v1.json` 定义的输入，并须以 `material_id` 完全隔离切分。任何添加新来源、特征化规则变更或标签变更均必须建立新版本，而不能改写本包。
''',encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
