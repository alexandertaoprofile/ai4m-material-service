"""Create D-level, non-screening seed records for the agreed robot-arm scope."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

FIELDS=("material_id","property","value_min","value_max","unit","condition","basis","source","temperature_min_K","temperature_max_K","estimate_version")
PROFILES={
 "al": [("density",2700,2850,"kg/m³"),("youngs_modulus",68,74,"GPa"),("yield_strength",250,520,"MPa"),("tensile_strength",320,580,"MPa"),("thermal_conductivity",110,190,"W/(m·K)"),("thermal_expansion_coefficient",22,24,"ppm/K")],
 "steel": [("density",7750,7900,"kg/m³"),("youngs_modulus",190,210,"GPa"),("yield_strength",650,1400,"MPa"),("tensile_strength",800,1550,"MPa"),("thermal_conductivity",15,45,"W/(m·K)"),("thermal_expansion_coefficient",10,14,"ppm/K")],
 "copper": [("density",8800,8950,"kg/m³"),("youngs_modulus",115,135,"GPa"),("yield_strength",180,550,"MPa"),("tensile_strength",250,650,"MPa"),("thermal_conductivity",250,390,"W/(m·K)"),("thermal_expansion_coefficient",16,18,"ppm/K")],
 "mg": [("density",1750,1850,"kg/m³"),("youngs_modulus",40,48,"GPa"),("yield_strength",120,200,"MPa"),("tensile_strength",180,280,"MPa"),("thermal_conductivity",45,75,"W/(m·K)"),("thermal_expansion_coefficient",24,28,"ppm/K")],
 "polymer": [("density",1000,1400,"kg/m³"),("youngs_modulus",1.0,4.0,"GPa"),("yield_strength",25,90,"MPa"),("tensile_strength",30,100,"MPa"),("thermal_conductivity",0.15,0.35,"W/(m·K)"),("thermal_expansion_coefficient",35,110,"ppm/K")],
 "cfpoly": [("density",1100,1500,"kg/m³"),("youngs_modulus",4,20,"GPa"),("tensile_strength",60,180,"MPa"),("thermal_conductivity",0.3,3,"W/(m·K)"),("thermal_expansion_coefficient",5,45,"ppm/K")],
 "laminate": [("density",1450,1650,"kg/m³"),("longitudinal_tensile_modulus",70,160,"GPa"),("longitudinal_tensile_strength",700,2200,"MPa"),("thermal_expansion_coefficient",-1,35,"ppm/K"),("glass_transition_temperature",120,250,"°C")],
}
ROWS=[
 ("7075-T6","7075-T6 铝合金","高强铝合金","7075-T6","al"),("2024-T3","2024-T3 铝合金","航空铝合金","2024-T3","al"),("6082-T6","6082-T6 铝合金","结构铝合金","6082-T6","al"),("17-4PH-H900","17-4PH H900 不锈钢","沉淀硬化不锈钢","17-4PH H900","steel"),("42CRMO-QT","42CrMo 调质钢","合金结构钢","42CrMo 调质","steel"),("AISI-4140-QT","AISI 4140 调质钢","合金结构钢","4140 调质","steel"),("20MNCR5","20MnCr5 渗碳钢","齿轮渗碳钢","20MnCr5","steel"),("18CRNIMO7-6","18CrNiMo7-6 渗碳钢","齿轮渗碳钢","18CrNiMo7-6","steel"),
 ("CUCRZR","CuCrZr 铜铬锆合金","导电/热管理铜合金","CuCrZr","copper"),("C11000","C11000 电解铜","导电铜","C11000","copper"),("A356-T6","A356-T6 铸造铝合金","铸造铝合金","A356-T6","al"),("ALS10MG","AlSi10Mg 增材铝合金","金属增材铝合金","AlSi10Mg","al"),("AZ91","AZ91 镁合金","铸造镁合金","AZ91","mg"),
 ("PA12-FDM","PA12 尼龙（FDM）","FDM 工程塑料","PA12","polymer"),("PA12-CF-FDM","PA12-CF（FDM）","FDM 短切碳纤维增强 PA12","PA12-CF","cfpoly"),("PA11-SLS","PA11 尼龙（SLS）","SLS 工程塑料","PA11","polymer"),("PP-FDM","PP 聚丙烯（FDM）","FDM 工程塑料","PP","polymer"),("TPU-FDM","TPU 弹性体（FDM）","FDM 弹性体","TPU","polymer"),("PEKK-FDM","PEKK（FDM）","FDM 高性能热塑性材料","PEKK","polymer"),("PPS-FDM","PPS（FDM）","FDM 高性能热塑性材料","PPS","polymer"),("PPS-CF-FDM","PPS-CF（FDM）","FDM 短切碳纤维增强 PPS","PPS-CF","cfpoly"),
 ("T700-EPOXY","T700/环氧连续碳纤维复材","连续碳纤维/环氧复材","T700/epoxy","laminate"),("IM7-8552","IM7/8552 连续碳纤维复材","连续碳纤维/环氧复材","IM7/8552","laminate"),("PC-CF-FDM","PC-CF（FDM）","FDM 短切碳纤维增强 PC","PC-CF","cfpoly"),("CF-PEKK-FDM","CF-PEKK（FDM）","FDM 短切碳纤维增强 PEKK","CF-PEKK","cfpoly"),("CF-PPS-FDM","CF-PPS（FDM）","FDM 短切碳纤维增强 PPS","CF-PPS","cfpoly")]
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists(): raise SystemExit('refusing to overwrite an existing bundle')
 a.output.mkdir(parents=True); materials=[]; aliases=[]; estimates=[]
 for key,name,family,grade,profile in ROWS:
  mid='MAT-ROBOT-SEED-'+key; state='D级工程估算身份；制造/热处理/温度范围见每条记录；待同状态来源事实替换'
  materials.append({"material_id":mid,"display_name":name,"family":family,"grade":grade,"UNS/standard":"","product_state":state,"source_id":"ENG-ROBOT-ARM-SEED-2026-08-25","data_role":"D级工程估算材料身份","temperature_coverage":"20–100 °C 初始预建模范围（复材/高温聚合物以每条条件为准）","composition_available":"材料体系/牌号已定义；待补充产品级成分与状态","process_metadata":"待补充厂商、打印/热处理及试样条件","notes":"仅 D 级工程估算；不参与目录筛选、排序或工程放行。","raw_source_file":"robot_arm_estimate_seed_2026-08-25","raw_sheet":"","raw_row_number":"","raw_row_json":json.dumps({"profile":profile})})
  aliases += [{"material_id":mid,"alias":x,"alias_type":"engineering_seed_alias","source":"ENG-ROBOT-ARM-SEED-2026-08-25"} for x in (grade,name)]
  for prop,lo,hi,unit in PROFILES[profile]:
   estimates.append({"material_id":mid,"property":prop,"value_min":lo,"value_max":hi,"unit":unit,"condition":state,"basis":f"按 {family} 的室温初始预建模保守区间；必须以同一产品状态的来源事实替换","source":"工程初步估算 v1（2026-08-25）","temperature_min_K":293.15,"temperature_max_K":373.15,"estimate_version":"v1"})
 for name,fields,data in (("materials.csv",tuple(materials[0]),materials),("material_aliases.csv",("material_id","alias","alias_type","source"),aliases),("engineering_estimates.csv",FIELDS,estimates),("property_points.csv",(),[]),("curve_data.csv",(),[]),("composition_long.csv",(),[])):
  with (a.output/name).open('w',encoding='utf-8',newline='') as f: w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)
 (a.output/'import_manifest.json').write_text(json.dumps({"counts":{"materials":len(materials),"engineering_estimates":len(estimates)},"scope":"robot-arm groups 1–4","rule":"D estimates are loaded only for presentation and structurally excluded from catalogue filtering/ranking."},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__': main()
