"""Unit-aware current-request conditions; no historical evidence parsing."""
import math
import re

NUM=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'


def normalize_units(text):
    text=text.replace('℃','°C').replace('摄氏度','°C').replace('−','-').replace('％','%')
    for token in ('GPa','MPa'):
        text=re.sub(r'(?<![A-Za-z])'+token+r'(?![A-Za-z])',token,text,flags=re.I)
    text=re.sub(r'ppm\s*/\s*°C','ppm/K',text,flags=re.I)
    text=re.sub(r'('+NUM+r')\s*[×*]\s*(10\s*\^?\s*-6|10⁻⁶)\s*/\s*(?:K|°C)',
                r'\1 ppm/K',text,flags=re.I)
    text=re.sub(r'([-+]?(?:\d+(?:\.\d*)?|\.\d+)[eE]-6)\s*/\s*(?:K|°C)',
                lambda m:str(float(m[1])*1e6)+' ppm/K',text)
    return text


def temperature_requirements(text,decisions):
    from src.alloy_workflow.contracts import MaterialContextError
    label=r'评价温度|测试温度|试验温度|服役温度|工作温度|(?<![\u4e00-\u9fffA-Za-z])温度'
    token='('+NUM+r')\s*(°\s*C|K)?(?![A-Za-z0-9.])'
    found=[]
    for m in re.finditer('(?:'+label+r')\s*(?:为|设为|取|是|[:：=])?\s*'+token,text,re.I):
        value,unit=float(m[1]),m[2]
        if re.search(r'不(?:要|用)|无需',text[max(0,m.start()-3):m.start()]):continue
        if not unit:
            if re.match(r'\s*[A-Za-z°]',text[m.end():]):
                raise MaterialContextError('评价温度单位尚未支持，保留原始条件开展设计。',text,[])
            unit='°C'
            decisions.append({'field':'test_temperature_C','parameter':'评价温度单位','adopted':m[1]+'°C',
                'reason':'数值未标单位，本次暂按摄氏度计算，可补充实际单位更新。'})
        found.append((m.start(),m.end(),value,re.sub(r'\s','',unit),m[0]))
    for m in re.finditer('('+NUM+r')\s*(°\s*C|K)\s*(?:下|时|条件下)\s*(?:的)?\s*(?:评价|测试|试验|拉伸|强度|屈服|抗拉|优化)',text,re.I):
        if not any(a<=m.start()<b for a,b,*_ in found):
            found.append((m.start(),m.end(),float(m[1]),re.sub(r'\s','',m[2]),m[0]))
    for m in re.finditer(r'(?:改为|改成|更正为|调整为)\s*('+NUM+r')\s*(°\s*C|K)',text,re.I):
        earlier=[r for r in found if r[1]<=m.start()]
        if earlier and re.fullmatch(r'[\s，,；;]*(?:再|现在)?\s*',text[max(earlier,key=lambda r:r[1])[1]:m.start()]):
            found.append((m.start(),m.end(),float(m[1]),re.sub(r'\s','',m[2]),m[0]))
    return [(v,u,span) for _,_,v,u,span in sorted(found)]


def composition_ranges(text,domain,decisions):
    from src.alloy_workflow.contracts import MaterialContextError
    result={}
    unit=r'wt\.?\s*%|at\.?\s*%|mol\.?\s*%|vol\.?\s*%|质量百分比|原子百分比|摩尔百分比|体积百分比|%'
    span=r'('+NUM+r')\s*('+unit+r')?\s*(?:到|至|[-–~～])\s*('+NUM+r')\s*('+unit+r')?'
    def bounds(m,kind):
        a,b=float(m[1]),float(m[3]);u1=m[2];u2=m[4]
        def basis(u):
            if not u or u=='%':return kind
            return {'质量百分比':'wt','原子百分比':'at','摩尔百分比':'mol','体积百分比':'vol'}.get(u,re.sub(r'[.\s%]','',u).lower())
        if basis(u1)!=kind or basis(u2)!=kind:
            raise MaterialContextError('配比单位与当前模型不同，先保留原单位开展配方设计。',text,[])
        if not (math.isfinite(a) and math.isfinite(b) and 0<=a<=b<=100):
            raise MaterialContextError('配比范围需满足0≤下限≤上限≤100%，保留原要求进行范围核对。',text,[])
        if not u1 and not u2:
            decisions.append({'field':'composition_bounds','parameter':'配比含量单位','adopted':kind+'.%',
                'reason':'含量未标单位，本次采用所选模型的配比单位。'})
        return [a,b]
    if domain=='short_cf_thermomechanical_rve_v1':
        if re.search(r'碳纤维(?:的)?质量分数[^，。；]{0,15}\d',text):
            raise MaterialContextError('碳纤维要求采用质量分数，当前模型使用体积分数；先保留原要求并规划密度换算。',text,[])
        for m in re.finditer(r'碳纤维(?:的)?体积分数\s*(?:为|控制在|在|[:：=])?\s*'+span,text,re.I):
            value=[v/100 for v in bounds(m,'vol')]
            result.setdefault('candidate_windows',{})['target_vf']=value
            result['target_vf']=sum(value)/2
        return result
    kind='mol' if domain=='chip_glass_thermomechanical_family_v1' else 'at' if domain=='hea_mpea' else 'wt'
    field={'mol':'oxide_bounds_mol_percent','at':'element_bounds_at_pct','wt':'element_bounds_wt_percent'}[kind]
    aliases=({'SiO2':'二氧化硅','Al2O3':'氧化铝','B2O3':'氧化硼','CaO':'氧化钙','MgO':'氧化镁',
              'BaO':'氧化钡','SrO':'氧化锶','Na2O':'氧化钠','SnO2':'二氧化锡'} if kind=='mol' else
             {'Fe':'铁','Cr':'铬','Ni':'镍','Mn':'锰','Si':'硅','C':'碳','N':'氮','Al':'铝','Cu':'铜',
              'Mg':'镁','Zn':'锌','Ti':'钛','Nb':'铌','Mo':'钼','Co':'钴','Zr':'锆','Ag':'银'})
    for symbol,name in aliases.items():
        ident=r'(?:'+name+r'\s*[（(]'+symbol+r'[）)]|'+name+r'|(?<![A-Za-z0-9])'+symbol+r'(?![A-Za-z]))'
        for m in re.finditer(ident+r'\s*(?:元素)?\s*(?:含量|比例)?\s*(?:为|控制在|在|[:：=])?\s*'+span,text,re.I):
            if re.search(r'不(?:要|用)|无需',text[max(0,m.start()-3):m.start()]):
                raise MaterialContextError('保留否定配比范围要求，先进行范围设计。',text,[])
            value=bounds(m,kind)
            old=result.setdefault(field,{}).get(symbol)
            if old and old!=value:
                value=[max(old[0],value[0]),min(old[1],value[1])]
                if value[0]>value[1]:raise MaterialContextError('同一组分范围无交集，保留两个方案分别设计。',text,[])
            result[field][symbol]=value
    return result
