#!/usr/bin/env python3
"""Import the 32 numbered 0--300 C glass examples in CN1275889C Table 1."""
from __future__ import annotations
import argparse, csv
from pathlib import Path
from bs4 import BeautifulSoup

CORE=("SiO2","Al2O3","B2O3","Na2O","K2O","Li2O","MgO","CaO","BaO","ZnO","TiO2","ZrO2","P2O5")
SOURCE=set(CORE)|{"SrO","As2O3","Sb2O3","CeO2","Y2O3","SnO2","Cl"}
def main() -> None:
 p=argparse.ArgumentParser();p.add_argument('--html',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists(): raise SystemExit(f'refusing to overwrite existing output: {a.output}')
 s=BeautifulSoup(a.html.read_text(encoding='utf-8'),'html.parser'); out=[]
 for index in range(4):
  rows=[[c.get_text('',strip=True).replace('−','-') for c in r.select(':scope>th,:scope>td')] for r in s.select('table')[index].select('tr')]
  samples=rows[0][1:]
  count=len(samples); values={r[0]:r[1:] for r in rows if len(r)==count+1 and r and r[0] in SOURCE}
  cte=next(r[1:] for r in rows if r and r[0].startswith('CTE('))
  for j,sample in enumerate(samples):
   if not sample.isdigit() or not cte[j]: continue
   comp={name:(series[j] or '0') for name,series in values.items()}; total=sum(float(v) for v in comp.values())
   other=';'.join(f'{name}={value} mol%' for name,value in comp.items() if name not in CORE and float(value))
   record={'record_id':f'CN1275889C-CTE-{sample}','glass_id':f'CN1275889C_example_{sample}','glass_display_name':f'CN1275889C 实例 {sample}','glass_family':'无碱铝硼硅酸盐显示玻璃（专利实例族）','composition_basis':'mol_percent','other_oxides_text':other,'composition_total':f'{total:.4f}','composition_source_batch_or_lot':f'CN1275889C Table 1, example {sample}','process_state':'专利实验玻璃；1000–25000 g 批料、约1625°C铂坩埚熔制4–16h；CTE按纤维伸长法报告','model_lane':'glass_thermal','property_name':'CTE_linear_0_to_300C','property_value':f'{float(cte[j])*1e-7:.9g}','property_unit':'1/K','temperature_min_C':'0','temperature_max_C':'300','test_method':'ASTM E228-85 fibre elongation（专利定义）','source_id':'SRC-PATENT-CN1275889C','source_locator':f'CN1275889C PDF; Google Patents HTML Table 1 segment {index+1}; example {sample}; CTE row','evidence_level':'B_published_patent_example','data_license':'public patent publication','review_notes':'Oxide mol% batch composition; CTE 0–300°C in 10^-7/°C converted to 1/K.'}
   for name in CORE: record[name]=comp.get(name,'0')
   out.append(record)
 if len(out)!=32: raise SystemExit(f'expected 32 numbered examples, got {len(out)}')
 fields=['record_id','glass_id','glass_display_name','glass_family','composition_basis',*CORE,'other_oxides_text','composition_total','composition_source_batch_or_lot','process_state','model_lane','property_name','property_value','property_unit','temperature_min_C','temperature_max_C','test_method','source_id','source_locator','evidence_level','data_license','review_notes']
 a.output.parent.mkdir(parents=True,exist_ok=True)
 with a.output.open('w',encoding='utf-8',newline='') as h: w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(out)
 print(f'wrote {len(out)} CTE records')
if __name__=='__main__':main()
