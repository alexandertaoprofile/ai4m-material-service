#!/usr/bin/env python3
"""Parse all locally available, identity-mapped base-alloy tensile tables.

This is intentionally an expansion of the *research* training layer only.  It
does not alter the mature product registry.  A row is admitted only when the
source document is mapped to an exact grade, the raw-audit has not marked the
table weld/join, an MPa or ksi strength field is explicit, and test temperature
is explicit or the caption says room-temperature.
"""
from __future__ import annotations
import argparse, csv, json, re
from pathlib import Path

META_PREFIX='lineage_'
def read(path):
 with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write(path,fields,rows):
 with path.open('w',encoding='utf8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
def num(x):
 try:return float(x)
 except (ValueError,TypeError):return None
def textnorm(x):return re.sub(r'[^a-z0-9]','',x.casefold())
def source_id(row):return row.get('lineage_source_table_id','')
def temperature(row, caption):
 # Exact test temperature takes precedence.  Heat-treatment temperatures never
 # act as a test temperature.  RT gets an explicit convention code.
 for key,val in row.items():
  low=key.casefold()
  if 'temperature' not in low or 'heat_treatment' in low or 'exposure' in low:continue
  n=num(val)
  if n is None:continue
  if 'deg_c' in low:return n+273.15,'source_degC'
  if 'deg_f' in low:return (n-32)*5/9+273.15,'source_degF'
 if re.search(r'room[ -]?temperature|room temperature|\bRT\b',caption,re.I):return 293.15,'room_temperature_convention_293.15K'
 return None,None
def property_columns(keys):
 for key in keys:
  low=key.casefold()
  if not any(x in low for x in ['yield_strength','tensile_strength','ultimate_tensile']):continue
  if 'ksi' in low: unit='ksi'
  elif 'mpa' in low: unit='MPa'
  else: continue
  prop='yield_strength' if 'yield' in low else 'ultimate_tensile_strength'
  yield key,prop,unit
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--raw-audit',type=Path,required=True);ap.add_argument('--registry',type=Path,required=True);ap.add_argument('--output-dir',type=Path,required=True);a=ap.parse_args()
 if a.output_dir.exists():raise SystemExit(f'refusing to overwrite existing output: {a.output_dir}')
 docs={x['lineage_document_id']:x for x in json.loads(a.registry.read_text())['documents']}
 output=[]; rejected=[];seen=set()
 for table in read(a.raw_audit):
  if table['category'] not in {'tensile','mechanical'}:continue
  if table['disposition']=='exclude_welding_or_join':
   rejected.append({**table,'reason':'audit excludes weld/join'});continue
  path=Path(table['local_path'])
  if not path.exists():
   rejected.append({**table,'reason':'local source file missing'});continue
  rows=read(path)
  if not rows:continue
  doc_ids={r.get('lineage_document_id','') for r in rows}
  if len(doc_ids)!=1 or next(iter(doc_ids)) not in docs:
   rejected.append({**table,'reason':'no exact document-to-grade registry mapping'});continue
  doc=docs[next(iter(doc_ids))]
  for raw in rows:
   caption=' '.join(raw.get(k,'') for k in ['lineage_caption','lineage_footnote'])
   if re.search(r'\bweld|welding|filler|joint|braze|solder',caption,re.I):continue
   t, t_source=temperature(raw,caption)
   if t is None:continue
   columns=list(property_columns(raw))
   # Many extracted tables carry the same value in both ksi and MPa.  Keep the
   # source MPa column when present; otherwise convert the explicit ksi value.
   chosen={}
   for col,prop,unit in columns:
    if prop not in chosen or unit=='MPa': chosen[prop]=(col,prop,unit)
   condition='; '.join(f'{k}={v}' for k,v in raw.items() if v and not k.startswith(META_PREFIX) and k not in {c[0] for c in columns} and 'temperature' not in k.casefold())
   for col,prop,unit in chosen.values():
    original=num(raw.get(col,''))
    if original is None:continue
    value_mpa=original if unit=='MPa' else original*6.894757293168361
    locator=f"{source_id(raw)}; page {raw.get('lineage_page_number','')}"
    key=(doc['material_id'],prop,round(value_mpa,6),round(t,6),locator,condition)
    if key in seen:continue
    seen.add(key)
    output.append({'material_id':doc['material_id'],'material_display_name':doc['display_name'],'property':prop,'value':f'{value_mpa:.8g}','unit':'MPa','temperature_K':f'{t:.8g}','temperature_source':t_source,'condition':condition,'source_id':raw.get('lineage_document_id',''),'source_locator':locator,'source_column':col,'source_caption':raw.get('lineage_caption',''),'source_footnote':raw.get('lineage_footnote',''),'raw_source_file':str(path),'raw_row_number':raw.get('lineage_source_item_index',''),'training_admission':'admitted_local_identity_mapped_base_tensile','exclusion_check':'not_weld_or_join; exact_registry_document_mapping'})
 a.output_dir.mkdir(parents=True)
 fields=list(output[0]) if output else []
 write(a.output_dir/'local_expanded_short_time_strength_observations.csv',fields,output)
 rejectfields=list(rejected[0]) if rejected else ['local_path','reason']
 write(a.output_dir/'table_rejection_queue.csv',rejectfields,rejected)
 summary={'local_tables_admitted_rows':len(output),'distinct_material_ids':len({r['material_id'] for r in output}),'room_temperature_convention_rows':sum(r['temperature_source'].startswith('room') for r in output),'rejected_tables':len(rejected),'policy':'Exact document-grade registry mapping, explicit strength unit, and explicit or room-temperature test condition only; registry/service unchanged.'}
 (a.output_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
