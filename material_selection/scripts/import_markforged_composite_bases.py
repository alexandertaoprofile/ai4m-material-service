"""Import the four fully specified Markforged FFF composite-base materials.

The source's continuous-fibre rows are intentionally excluded: it names the
fibre but not the resin/fibre construction for each numeric specimen.
"""
from __future__ import annotations
import argparse, csv, hashlib, json
from pathlib import Path

SOURCE = "Markforged_Composites_Data_Sheet.pdf"; SOURCE_ID = "SRC-MARKFORGED-COMPOSITES-REV5-2021"
FIELDS = ("material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind", "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json")
MATERIALS = [("ONYX", "Markforged Onyx（短切碳纤维增强尼龙）", "FFF 微碳纤维增强尼龙", "Onyx", [2.4,40,37,25,71,3.0,145,330,1.2]), ("ONYX-FR", "Markforged Onyx FR（阻燃短切碳纤维增强尼龙）", "FFF 阻燃短切碳纤维增强尼龙", "Onyx FR", [3.0,41,40,18,71,3.6,145,None,1.2]), ("ONYX-ESD", "Markforged Onyx ESD（ESD 短切碳纤维增强尼龙）", "FFF ESD 短切碳纤维增强尼龙", "Onyx ESD", [4.2,52,50,25,83,3.7,138,44,1.2]), ("NYLON-WHITE", "Markforged Nylon White", "FFF 未增强尼龙", "Nylon White", [1.7,51,36,150,50,1.4,41,110,1.1])]
PROPS = (("youngs_modulus", "GPa"), ("yield_strength", "MPa"), ("tensile_strength", "MPa"), ("elongation", "%"), ("flexural_strength", "MPa"), ("flexural_modulus", "GPa"), ("heat_deflection_temperature", "°C"), ("izod_impact_strength_notched", "J/m"), ("density", "g/cm³"))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if not a.source.is_file() or a.source.name!=SOURCE: raise SystemExit(f'expected {SOURCE}')
 if a.output.exists(): raise SystemExit('refusing to overwrite an existing import bundle')
 ms=[]; points=[]; aliases=[]
 for suffix,name,family,grade,values in MATERIALS:
  mid=f'MAT-MARKFORGED-{suffix}'; condition=f'{grade}; FFF printed composite-base-only plaque; full infill; source test specimen and method as stated; 23 °C unless property states otherwise'
  ms.append({"material_id":mid,"display_name":name,"family":family,"grade":grade,"UNS/standard":"","product_state":"FFF 打印件；仅复合基材、全填充；不含连续纤维","source_id":SOURCE_ID,"data_role":"manufacturer 3D-print material evidence","temperature_coverage":"23–145 °C（各性质见条件）","composition_available":"manufacturer product identity; exact formulation not disclosed","process_metadata":"composite-base-only; full infill; no continuous fibre","notes":"连续纤维增强数值因来源未逐项说明基材/构型，未与本材料混入。","raw_source_file":SOURCE,"raw_sheet":"","raw_row_number":"1","raw_row_json":json.dumps({"revision":"5.0","date":"2021-08-01"})})
  aliases += [{"material_id":mid,"alias":x,"alias_type":"trade_name","source":SOURCE_ID} for x in (grade, name)]
  for (prop,unit),value in zip(PROPS,values):
   if value is None: continue
   points.append({"material_id":mid,"property":prop,"value":str(value),"unit":unit,"temperature_K":"296.15","uncertainty":"","data_kind":"manufacturer_typical_value","condition":condition,"source_id":SOURCE_ID,"source_locator":"Composites Material Datasheet Rev 5.0; page 1","notes":"Manufacturer typical value; not a design specification.","raw_source_file":SOURCE,"raw_sheet":"","raw_row_number":"1","raw_row_json":json.dumps({"property":prop,"value":value,"unit":unit},sort_keys=True)})
 a.output.mkdir(parents=True)
 for name,fields,data in (("materials.csv",tuple(ms[0]),ms),("property_points.csv",FIELDS,points),("material_aliases.csv",("material_id","alias","alias_type","source"),aliases),("curve_data.csv",(),[]),("composition_long.csv",(),[])):
  with (a.output/name).open('w',encoding='utf-8',newline='') as h: w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(data)
 (a.output/'import_manifest.json').write_text(json.dumps({"source":{"file":SOURCE,"sha256":hashlib.sha256(a.source.read_bytes()).hexdigest(),"page":1},"counts":{"materials":len(ms),"property_points":len(points)},"included":"Four fully specified FFF composite-base material rows.","excluded":"Continuous-fibre values; source does not link their numeric specimens to a resin/fibre construction."},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__': main()
