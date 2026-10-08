#!/usr/bin/env python3
"""Recover same-sample thermo-mechanical fields paired to the 0--300 C CTE."""
from __future__ import annotations
import argparse,csv
from pathlib import Path
from bs4 import BeautifulSoup
def cells(r):return [c.get_text('',strip=True).replace('−','-') for c in r.select(':scope>th,:scope>td')]
def number(v):return '' if v in {'','—','-'} else v
def main():
 p=argparse.ArgumentParser();p.add_argument('--html',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise SystemExit(f'refusing to overwrite existing output: {a.output}')
 s=BeautifulSoup(a.html.read_text(encoding='utf-8'),'html.parser');out=[]
 for index in range(3,24,2):
  table=2+(index-3)//2;rows=[cells(r) for r in s.select('table')[index].select('tr')];k=next(i for i,r in enumerate(rows) if r and r[0] in {'Physical Properties','Physical'});samples=rows[k][1:];n=len(samples)
  wanted={'cte_0_300_x1e7_per_C':lambda x:x.startswith('CTE 0-300'),'strain_point_fibre_C':lambda x:x.startswith('Strain Point') and 'BBV' not in x,'strain_point_BBV_C':lambda x:x.startswith('Strain Point (BBV)'),'annealing_point_fibre_C':lambda x:x.startswith('Annealing Point') and 'BBV' not in x,'annealing_point_BBV_C':lambda x:x.startswith('Annealing Point (BBV)'),'softening_point_fibre_C':lambda x:x.startswith('Softening Point'),'density_g_per_cm3':lambda x:x.startswith('Density'),'young_modulus_GPa':lambda x:x.startswith("Young's"),'shear_modulus_GPa':lambda x:x.startswith('Shear Modulus'),'poisson_ratio':lambda x:x.startswith("Poisson"),'stress_optical_coefficient_nm_cm_per_MPa':lambda x:x.startswith('SOC'),'viscosity_temperature_200_poise_C':lambda x:x.startswith('T(200P)'),'viscosity_temperature_35kpoise_C':lambda x:x.startswith('T(35kP)'),'liquidus_viscosity_kpoise':lambda x:x.startswith('Liquidus viscosity')}
  fields={}
  for r in rows[k+1:]:
   if len(r)==n+1:
    for name,match in wanted.items():
     if match(r[0]):fields[name]=r[1:]
  for j,sample in enumerate(samples):
   if 'cte_0_300_x1e7_per_C' not in fields or not number(fields['cte_0_300_x1e7_per_C'][j]):continue
   row={'glass_id':f'US20250026678_sample_{sample}','source_id':'SRC-PATENT-US20250026678','source_locator':f'US20250026678A1 PDF; Google Patents HTML Table {table}; example {sample}','composition_basis':'mol_percent','cte_temperature_range_C':'0-300','cte_method':'ASTM E228 (publication definition)'}
   for name,series in fields.items():row[name]=number(series[j])
   out.append(row)
 fields=['glass_id','source_id','source_locator','composition_basis','cte_temperature_range_C','cte_method','cte_0_300_x1e7_per_C','strain_point_fibre_C','strain_point_BBV_C','annealing_point_fibre_C','annealing_point_BBV_C','softening_point_fibre_C','density_g_per_cm3','young_modulus_GPa','shear_modulus_GPa','poisson_ratio','stress_optical_coefficient_nm_cm_per_MPa','viscosity_temperature_200_poise_C','viscosity_temperature_35kpoise_C','liquidus_viscosity_kpoise']
 with a.output.open('w',encoding='utf-8',newline='') as h:w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(out)
 print(f'wrote {len(out)} same-identity rows')
if __name__=='__main__':main()
