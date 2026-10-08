"""Versioned repair of existing admitted inputs; never writes serving repositories.

CSV files are reproducible ML intermediates, not authored spreadsheet workbooks.
No targets, compositions, record membership or frozen split roles are changed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd

ROOT = Path('/data/se42/experiments/alloy_foundation_v0_20260916')
SOURCE = ROOT/'process_adapter_v2_20260920/dataset_snapshot.csv'
SCHEMA = 'alloy_process_repair_v6_20260923'
STAGE_KEYS = [
    'solution_stage_1_temp_C', 'solution_stage_1_time_h',
    'precipitation_stage_1_temp_C', 'precipitation_stage_1_time_h',
    'precipitation_stage_2_temp_C', 'precipitation_stage_2_time_h',
    'precipitation_stage_2_heating_C_per_h',
    'precipitation_stage_3_temp_C', 'precipitation_stage_3_time_h',
]
MICRO = ['desc_v6_grain_size_um', 'desc_v6_grain_areal_density_per_mm2']
PHASES = ['FCC','BCC','HCP','B2','L12']
MICRO += ['desc_v6_phase_'+p for p in PHASES]
EXTRAS = ['desc_v6_'+k for k in STAGE_KEYS] + ['desc_v6_test_strain_rate_per_s'] + MICRO
PROTECTED = ['record_id','domain','task','target','composition_json','composition_key',
             'test_temperature_C','temperature_missing','role','process_label']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def number(value):
    if value is None or pd.isna(value) or str(value).strip() == '':
        return None
    try:
        x=float(value)
    except (TypeError,ValueError):
        raise ValueError(f'Non-numeric explicit value: {value!r}')
    if not math.isfinite(x):
        raise ValueError(f'Nonfinite explicit value: {value!r}')
    return x


def convert(value, source_unit, target_unit):
    x=number(value)
    if x is None:return None
    if source_unit==target_unit:return x
    if (source_unit,target_unit)==('K','C'):
        if x<=0:raise ValueError('Absolute temperature must be positive')
        return x-273.15
    if (source_unit,target_unit)==('s','h'):return x/3600
    raise ValueError(f'Unsupported conversion {source_unit} -> {target_unit}')


def parse_stages(text):
    """Explicit key=value records only; reject unknown stages instead of truncation."""
    if pd.isna(text) or not str(text).strip():return {}
    result={}
    for token in str(text).split(';'):
        if not token.strip():continue
        match=re.fullmatch(r'\s*([A-Za-z0-9_]+)\s*=\s*([-+0-9.eE]+)\s*',token)
        if not match or match[1] not in STAGE_KEYS:
            raise ValueError(f'Unsupported heat treatment token: {token}')
        key,value=match[1],number(match[2])
        if key in result and result[key]!=value:raise ValueError(f'Conflicting stage: {key}')
        if key.endswith('_time_h') and value<0:raise ValueError(key)
        if key.endswith('_temp_C') and not -273.15<value<3000:raise ValueError(key)
        result[key]=value
    return result


def phase_flags(text):
    if pd.isna(text):return {}
    tokens=set(re.findall(r'[A-Za-z][A-Za-z0-9]*',str(text).upper()))
    if not tokens.intersection(PHASES):return {}
    return {'desc_v6_phase_'+p:float(p in tokens) for p in PHASES}


def source_features(domain, row):
    """Single mapping used for rebuilding and unit tests. No target inputs."""
    values={}; evidence=[]
    def put(out, field, iu, ou=None):
        ou=ou or iu
        value=convert(row.get(field),iu,ou)
        if value is None:return
        if ou in ('h','um','mm^-2','s^-1') and value<0:raise ValueError(field)
        if ou in ('um','mm^-2','s^-1') and value<=0:raise ValueError(field)
        if ou=='pct' and not 0<=value<=100:raise ValueError(field)
        if ou=='C' and not -273.15<value<3000:raise ValueError(field)
        values[out]=value
        evidence.append(dict(feature=out,source_field=field,source_value=number(row.get(field)),
                             source_unit=iu,value=value,unit=ou))
    if domain=='hea_mpea':
        for field,out,iu,ou in [
            ('homogenization_temp_K','homogenization_temp_C','K','C'),
            ('annealing_temp_K','annealing_temp_C','K','C'),
            ('annealing_time_h','annealing_time_h','h','h'),
            ('aging_temp_K','aging_temp_C','K','C'),
            ('aging_time_h','aging_time_h','h','h'),
            ('cold_rolling_reduction_pct','cold_reduction_pct','pct','pct')]:
            put('desc_v2_'+out,field,iu,ou)
        put('desc_v6_grain_size_um','grain_size_um','um')
        for key,v in phase_flags(row.get('phase_microstructure')).items():
            values[key]=v
            evidence.append(dict(feature=key,source_field='phase_microstructure',
                                 source_value=str(row.phase_microstructure),value=v,
                                 source_unit='phase_label',unit='indicator'))
    elif domain=='stainless':
        put('desc_v2_solution_temp_C','Solution_treatment_temperature','K','C')
        put('desc_v2_solution_time_h','Solution_treatment_time(s)','s','h')
        put('desc_v6_grain_areal_density_per_mm2','Grains mm-2','mm^-2')
        for field,out in [('Water_Quenched_after_s.t.','quench'),('Air_Quenched_after_s.t.','aircool')]:
            v=number(row.get(field))
            if v is not None:
                if v not in (0,1):raise ValueError(field)
                put('desc_v2_flag_'+out,field,'indicator')
    elif domain=='nickel_superalloy':
        stages=parse_stages(row.get('heat_treatment'))
        for key,value in stages.items():
            out='desc_v6_'+key;values[out]=value
            unit='C' if key.endswith('_C') else 'h' if key.endswith('_h') and 'heating_' not in key else 'C/h'
            evidence.append(dict(feature=out,source_field='heat_treatment/'+key,
                source_value=value,source_unit=unit,value=value,unit=unit))
        for suffix in ('temp_C','time_h'):
            key='solution_stage_1_'+suffix
            if key in stages:
                values['desc_v2_solution_'+suffix]=stages[key]
                evidence.append(dict(feature='desc_v2_solution_'+suffix,source_field='heat_treatment/'+key,
                    source_value=stages[key],value=stages[key],source_unit='C' if suffix=='temp_C' else 'h',unit='C' if suffix=='temp_C' else 'h'))
        # Multi-stage precipitation is never collapsed into the legacy aging slot.
        if any(k.startswith('precipitation_') for k in stages):values['desc_v2_flag_age']=1.
    elif domain=='copper':
        put('desc_v6_test_strain_rate_per_s','test_strain_rate_per_s','s^-1')
    for stage,flag in [('solution','solution'),('aging','age'),('annealing','anneal'),('homogenization','homogenize')]:
        if any('desc_v2_'+stage+'_'+s in values for s in ['temp_C','time_h']):
            values['desc_v2_flag_'+flag]=1.
    if values.get('desc_v2_cold_reduction_pct',0)>0:values['desc_v2_flag_coldwork']=1.
    return values,evidence


def build(out):
    magpie=Path('/data/mamba/envs/crabnet-py310/lib/python3.10/site-packages/crabnet/data/element_properties/magpie.csv')
    ATOMIC_WEIGHT=pd.read_csv(magpie).set_index('element').AtomicWeight.to_dict()
    def normalize_composition(values,basis):
        # Exact positive-value, normalization and trace cutoff semantics of the
        # original loader, without importing its unrelated CrabNet dependency.
        c={e:number(v) for e,v in values.items() if e in ATOMIC_WEIGHT and number(v) is not None and number(v)>0}
        if basis=='wt':c={e:v/ATOMIC_WEIGHT[e] for e,v in c.items()}
        total=sum(c.values())
        return {e:v/total for e,v in c.items() if v/total>1e-8}
    original=pd.read_csv(SOURCE,low_memory=False)
    assert original.record_id.is_unique
    frame=original.copy()
    for col in EXTRAS:
        frame[col]=np.nan;frame[col+'_missing']=1.
    for col in ['repair_source_row','repair_process_raw','repair_phase_raw','repair_state_id']:
        frame[col]=''
    sources={str(SOURCE):sha(SOURCE),str(magpie):sha(magpie)};joins=[];provenance=[];conflicts=[]
    domains=['hea_mpea','stainless','nickel_superalloy','copper']
    for domain in domains:
        subset=frame[frame.domain.eq(domain)]
        paths=subset.source.unique();assert len(paths)==1
        path=Path(paths[0]);sources[str(path)]=sha(path)
        raw=pd.read_csv(path,low_memory=False)
        lookup=raw.set_index('record_id',drop=False) if domain=='hea_mpea' else raw
        if domain=='hea_mpea':assert lookup.index.is_unique
        for index,rec in subset.iterrows():
            rid=rec.record_id
            key=rid.rsplit(':',1)[0] if domain=='hea_mpea' else int(re.fullmatch(r'(?:ss|cu)-(\d+):(YS|UTS)|ni-(\d+)-(YS|UTS)',rid)[1] or re.fullmatch(r'ni-(\d+)-(YS|UTS)',rid)[1])
            row=lookup.loc[key]
            if domain=='hea_mpea':
                target=row['yield_strength_02_offset_MPa' if rec.task=='YS' else 'ultimate_tensile_strength_MPa']
                comp=normalize_composition(json.loads(row.composition_at_pct),'at')
                temp=row.test_temperature_C;rawproc=row.processing_method
            elif domain=='stainless':
                target=row['yield_0p2_MPa' if rec.task=='YS' else 'uts_MPa']
                c={e:row[e] for e in ATOMIC_WEIGHT if e in raw.columns and e!='Fe'}
                c['Fe']=100-sum(number(v) or 0 for v in c.values())
                comp=normalize_composition(c,'wt');temp=row['Temperature (K)']-273.15
                rawproc=json.dumps({k:number(row[k]) for k in ['Solution_treatment_temperature','Solution_treatment_time(s)','Water_Quenched_after_s.t.','Air_Quenched_after_s.t.','Product form']})
            else:
                target=row.value_MPa if domain=='nickel_superalloy' else row['yield_0p2_MPa' if rec.task=='YS' else 'uts_MPa']
                comp=normalize_composition({c.removesuffix('_wt_percent'):row[c] for c in raw if c.endswith('_wt_percent')},'wt')
                temp=row.test_temperature_C
                rawproc=row.heat_treatment if domain=='nickel_superalloy' else row.alloy_state
            assert np.isclose(target,rec.target,atol=1e-7),('target mismatch',rid)
            if rec.temperature_missing==0:assert np.isclose(temp,rec.test_temperature_C,atol=1e-7),('temperature mismatch',rid)
            old=json.loads(rec.composition_json)
            assert set(comp)==set(old) and max(abs(comp[e]-old[e]) for e in comp)<1e-6,('composition mismatch',rid)
            vals,ev=source_features(domain,row)
            for col,value in vals.items():
                prior=number(frame.at[index,col])
                # Existing nonmissing numeric values must agree; no silent overwrite.
                if not col.startswith('desc_v2_flag_') and prior is not None and not np.isclose(prior,value,rtol=1e-7,atol=1e-7):
                    conflicts.append(dict(record_id=rid,field=col,old=prior,new=value))
                    continue
                frame.at[index,col]=value
                if col+'_missing' in frame:frame.at[index,col+'_missing']=0.
            frame.at[index,'repair_source_row']=str(key)
            frame.at[index,'repair_process_raw']='' if pd.isna(rawproc) else str(rawproc)
            frame.at[index,'repair_phase_raw']=str(row.get('phase_microstructure',''))
            frame.at[index,'repair_state_id']=str(row.get('state_group',row.get('tag',row.get('alloy_state',''))))
            joins.append(dict(record_id=rid,source_file=str(path),source_key=str(key),
                              source_sha256=sources[str(path)],target_temperature_composition_verified=True))
            provenance.extend(dict(record_id=rid,domain=domain,source_file=str(path),source_key=str(key),**e) for e in ev)
    if conflicts:raise ValueError(json.dumps(conflicts[:20]))
    pd.testing.assert_frame_equal(frame[PROTECTED],original[PROTECTED])
    assert frame.groupby('composition_key').role.nunique().max()==1
    for col in frame:
        if col.endswith('_missing') and col[:-8] in frame and col.startswith('desc_'):
            assert np.array_equal(frame[col].to_numpy(),frame[col[:-8]].isna().astype(float).to_numpy()),col
    out.mkdir(exist_ok=False)
    frame.to_csv(out/'dataset_repaired.csv',index=False)
    frame[PROTECTED].to_csv(out/'frozen_records.csv',index=False)
    counts=[]
    for domain,g in frame.groupby('domain'):
        old=original[original.domain==domain]
        for col in [c for c in frame if c.startswith('desc_') and not c.endswith('_missing')]:
            before=int(old[col].notna().sum()) if col in old else 0
            after=int(g[col].notna().sum())
            if before!=after:counts.append(dict(domain=domain,feature=col,before=before,after=after))
    save(out/'FIELD_COVERAGE.json',counts)
    save(out/'FIELD_PROVENANCE.json',provenance)
    save(out/'SOURCE_JOINS.json',joins)
    save(out/'SOURCE_HASHES.json',sources)
    numeric=[c for c in frame if c.startswith('desc_')]
    save(out/'FEATURE_SCHEMA.json',dict(version=SCHEMA,extra_fields=EXTRAS,
        numeric_features=numeric,microstructure_fields=MICRO,
        stage_fields=STAGE_KEYS,grain_density_not_converted_to_diameter=True,
        stainless_solution_temperature_unit_evidence='/data/se42/hea_surrogate/src/api/reusable_rocket_stainless_runtime.py:_rows uses solution_treatment_temperature_K',
        known_missing_masks=True,unchanged_process_vocabulary=True,
        metadata_excluded_from_features=['source','repair_process_raw','repair_phase_raw','repair_state_id','repair_source_row'],
        unsupported_new_stages='fail, extend schema explicitly; never silently truncate'))
    audit=dict(schema=SCHEMA,rows=len(frame),composition_groups=frame.composition_key.nunique(),
        mapped_task_rows=len(joins),source_fields_retained=len(provenance),
        roles=frame.role.value_counts().to_dict(),protected_columns_unchanged=True,
        grain_units_separate=True,conflicts=conflicts,original_snapshot_sha256=sha(SOURCE),
        repaired_snapshot_sha256=sha(out/'dataset_repaired.csv'),
        no_new_rows_admitted=True,primary_source_reaudit=False,production_release_authorized=False)
    save(out/'DATA_AUDIT.json',audit)
    print(json.dumps(audit),flush=True)
    return frame


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    build(p.parse_args().out)


if __name__=='__main__':main()
