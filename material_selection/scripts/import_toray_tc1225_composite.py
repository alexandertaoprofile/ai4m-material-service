"""Import two PDF-verified TC1225/T700GC directional composite records."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

MID="MAT-COMP-TC1225-T700GC-UNITAPE"
DOC="fad1177ac8e7"
FIELDS=("material_id","property","value","unit","temperature_K","uncertainty","data_kind","condition","source_id","source_locator","notes","raw_source_file","raw_sheet","raw_row_number","raw_row_json")
SPECS={
 "fad1177ac8e7-table-0049": [("longitudinal_tensile_strength","strength_ksi","MPa",6.894757293168),("longitudinal_tensile_modulus","modulus_msi","GPa",6.894757293168),("longitudinal_tensile_poissons_ratio","poisson_s_ratio","dimensionless",1)],
 "fad1177ac8e7-table-0053": [("longitudinal_tensile_strength","strength_ksi","MPa",6.894757293168),("longitudinal_tensile_modulus","modulus_msi","GPa",6.894757293168),("longitudinal_tensile_poissons_ratio","poisson_s_ratio","dimensionless",1)],
 "fad1177ac8e7-table-0057": [("longitudinal_tensile_strength","strength_ksi","MPa",6.894757293168),("longitudinal_tensile_modulus","modulus_msi","GPa",6.894757293168),("longitudinal_tensile_poissons_ratio","poisson_s_ratio","dimensionless",1)],
 "fad1177ac8e7-table-0065": [("longitudinal_tensile_strength","strength_ksi","MPa",6.894757293168),("longitudinal_tensile_modulus","modulus_msi","GPa",6.894757293168),("longitudinal_tensile_poissons_ratio","poisson_s_ratio","dimensionless",1)],
 "fad1177ac8e7-table-0108": [("laminate_0_90_unnotched_compressive_strength","strength_ksi","MPa",6.894757293168),("laminate_0_90_unnotched_compressive_modulus","modulus_msi","GPa",6.894757293168)],
 "fad1177ac8e7-table-0117": [("laminate_0_90_unnotched_compressive_strength","strength_ksi","MPa",6.894757293168),("laminate_0_90_unnotched_compressive_modulus","modulus_msi","GPa",6.894757293168)],
 "fad1177ac8e7-table-0121": [("laminate_0_90_unnotched_compressive_strength","strength_ksi","MPa",6.894757293168),("laminate_0_90_unnotched_compressive_modulus","modulus_msi","GPa",6.894757293168)],
}
CONDITIONS={
 "fad1177ac8e7-table-0049":"TC1225 LM PAEK / T700GC 12K T1E Unitape; 145 gsm; 34% resin; NMS 122/1; NPS 81225; 8-ply longitudinal tension; CTA -65°F (-54°C); measured values; PDF p.97",
 "fad1177ac8e7-table-0053":"TC1225 LM PAEK / T700GC 12K T1E Unitape; 145 gsm; 34% resin; NMS 122/1; NPS 81225; 8-ply longitudinal tension; RTA 70°F (21°C); measured values; PDF p.99",
 "fad1177ac8e7-table-0057":"TC1225 LM PAEK / T700GC 12K T1E Unitape; 145 gsm; 34% resin; NMS 122/1; NPS 81225; 8-ply longitudinal tension; ETA1 275°F (135°C); measured values; PDF p.101",
 "fad1177ac8e7-table-0065":"TC1225 LM PAEK / T700GC 12K T1E Unitape; 145 gsm; 34% resin; NMS 122/1; NPS 81225; 8-ply longitudinal tension; ETW wet 275°F (135°C); measured values; PDF p.105",
 "fad1177ac8e7-table-0108":"TC1225 LM PAEK / T700GC 12K T1E Unitape; 145 gsm; 34% resin; NMS 122/1; NPS 81225; 16-ply 50/0/50 unnotched compression 0/90; CTA -65°F (-54°C); measured values; PDF p.137",
 "fad1177ac8e7-table-0117":"TC1225 LM PAEK / T700GC 12K T1E Unitape; 145 gsm; 34% resin; NMS 122/1; NPS 81225; 16-ply 50/0/50 unnotched compression 0/90; ETA2 400°F (204°C); measured values; PDF p.143",
 "fad1177ac8e7-table-0121":"TC1225 LM PAEK / T700GC 12K T1E Unitape; 145 gsm; 34% resin; NMS 122/1; NPS 81225; 16-ply 50/0/50 unnotched compression 0/90; ETW wet 275°F (135°C); measured values; PDF p.145",
}
def main():
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists(): raise SystemExit('refusing to overwrite existing bundle')
 if not (a.input/'snapshot_manifest.json').is_file(): raise SystemExit('input must be completed snapshot')
 rows=[]
 for f in a.input.glob('*Toray*.csv'):
  with f.open(encoding='utf-8-sig',newline='') as h:
   by={r['lineage_source_table_id']:r for r in csv.DictReader(h) if r.get('statistic')=='Average' and r.get('lineage_source_table_id') in SPECS}
   for tid,r in by.items():
    for prop,col,unit,scale in SPECS[tid]:
     rows.append({"material_id":MID,"property":prop,"value":f"{float(r[col])*scale:g}","unit":unit,"temperature_K":"219.8167","uncertainty":"source average; standard deviation and specimen count retained in raw table","data_kind":"source_table_statistic_average","condition":CONDITIONS[tid],"source_id":DOC,"source_locator":f"{tid}; page {r['lineage_page_number']}","notes":"Directional composite property; excluded from isotropic metal-property comparison.","raw_source_file":f.name,"raw_sheet":"","raw_row_number":r['lineage_source_item_index'],"raw_row_json":json.dumps(r,ensure_ascii=False,sort_keys=True)})
 # DMA/DSC summary averages are material thermal-analysis evidence.  Ambient
 # and wet samples remain separate; they do not inherit a structural layup.
 for f in a.input.glob('*Toray*.csv'):
  with f.open(encoding='utf-8-sig',newline='') as h:
   for r in csv.DictReader(h):
    if r.get('sample')!='Average': continue
    tid=r['lineage_source_table_id']
    if tid in {'fad1177ac8e7-table-0448','fad1177ac8e7-table-0449'}:
     env='ambient' if tid.endswith('0448') else 'wet'
     for prop,col in [('glass_transition_temperature_dma_onset','onset_storage_modulus_t_g_circ_c'),('glass_transition_temperature_dma_tan_delta_peak','peak_of_tangent_delta_t_g_circ_c')]:
      rows.append({"material_id":MID,"property":prop,"value":r[col],"unit":"°C","temperature_K":"","uncertainty":"source average; standard deviation retained in raw table","data_kind":"source_table_statistic_average","condition":f"TC1225 LM PAEK / T700GC Unitape; DMA ASTM D7028; {env}; sample group summary; PDF p.401","source_id":DOC,"source_locator":f"{tid}; page 401","notes":"Thermal-analysis result; not a structural directional modulus.","raw_source_file":f.name,"raw_sheet":"","raw_row_number":r['lineage_source_item_index'],"raw_row_json":json.dumps(r,ensure_ascii=False,sort_keys=True)})
    if tid=='fad1177ac8e7-table-0450':
     for prop,col in [('glass_transition_temperature_dsc','glass_transition_temperature_t_g_deg_c'),('melting_onset_temperature','melting_onset_temperature_t_mo_deg_c'),('melting_peak_temperature','melting_peak_temperature_t_mp_deg_c'),('crystallization_onset_temperature','crystallization_onset_temperature_t_co_deg_c'),('hot_crystallization_peak_temperature','hot_crystallization_peak_temperature_t_cp_deg_c')]:
      rows.append({"material_id":MID,"property":prop,"value":r[col],"unit":"°C","temperature_K":"","uncertainty":"source average; standard deviation retained in raw table","data_kind":"source_table_statistic_average","condition":"TC1225 LM PAEK / T700GC Unitape; DSC ASTM D3418; sample group summary; PDF p.404","source_id":DOC,"source_locator":"fad1177ac8e7-table-0450; page 404","notes":"Thermal-analysis result; not a structural directional modulus.","raw_source_file":f.name,"raw_sheet":"","raw_row_number":r['lineage_source_item_index'],"raw_row_json":json.dumps(r,ensure_ascii=False,sort_keys=True)})
 if len(rows)!=27: raise ValueError(f'expected 27 values, got {len(rows)}')
 a.output.mkdir(parents=True)
 material={"material_id":MID,"display_name":"Toray Cetex TC1225/T700GC 单向带复材","family":"连续碳纤维增强热塑性复合材料","grade":"TC1225 (LM PAEK) / T700GC 12K T1E","UNS/standard":"NMS 122/1","product_state":"NPS 81225；具体试样方向和环境见性质条件","source_id":DOC,"data_role":"directional composite evidence","temperature_coverage":"CTA -65°F","composition_available":"yes","process_metadata":"145 gsm；34% resin","notes":"方向专属性质","raw_source_file":"TorayTC1225UnitapeCAM-RP-2019-036RevA5.10.2021MPDRFinal.pdf","raw_sheet":"","raw_row_number":"27","raw_row_json":"PDF p.27 material identity"}
 for name,fields,data in [('materials.csv',tuple(material),[material]),('property_points.csv',FIELDS,rows),('curve_data.csv',(),[]),('composition_long.csv',(),[]),('material_aliases.csv',('material_id','alias','alias_type','source'),[{"material_id":MID,"alias":"TC1225/T700GC","alias_type":"reviewed_product_identity","source":DOC}])]:
  with (a.output/name).open('w',encoding='utf-8',newline='') as h:
   w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(data)
 (a.output/'import_manifest.json').write_text(json.dumps({"counts":{"materials":1,"property_points":len(rows)},"included_tables":[*SPECS,'fad1177ac8e7-table-0448','fad1177ac8e7-table-0449','fad1177ac8e7-table-0450'],"exclusion":"directional properties do not map to generic isotropic strength/modulus"},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({"property_points":len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
