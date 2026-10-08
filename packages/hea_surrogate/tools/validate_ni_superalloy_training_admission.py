#!/usr/bin/env python3
"""Fail fast if the frozen Ni-superalloy admission package violates its rules."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

def read(path):
    with path.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))
def number(v):
    try: float(v); return True
    except (TypeError,ValueError): return False
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('package',type=Path); a=ap.parse_args(); p=a.package
    manifest=json.loads((p/'training_admission_manifest.json').read_text())
    cards=read(p/'grade_cards.csv'); comps=read(p/'composition_specification_envelopes.csv'); labels=read(p/'short_time_strength_observations.csv')
    assert manifest['training_model_started'] is False
    assert manifest['mature_product_service_changed'] is False
    assert 'Cambridge' in manifest['explicitly_excluded_source']
    ids={r['material_id'] for r in cards}
    assert ids == {r['material_id'] for r in comps}
    assert all(r['property'] in {'yield_strength','ultimate_tensile_strength'} for r in labels)
    assert all(number(r['value']) and number(r['temperature_K']) for r in labels)
    assert all(r['composition_link']=='exact_material_id' for r in labels)
    assert all(r['material_id'] in ids for r in labels)
    assert all(r['composition_representation']=='grade_specification_envelope' for r in comps)
    assert all(r['source_pdf'] or r['composition_origin']=='previously_admitted_structured_component' for r in comps)
    print(f'OK: {len(cards)} grade cards; {len(comps)} components; {len(labels)} short-time labels')
if __name__=='__main__': main()
