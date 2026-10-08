"""Convert RESOLVED objectives to an explicit numerical optimization contract.
Unknown goals/properties/units are never guessed or quietly dropped.
"""
ALIASES={'yield_strength_MPa':'YS_MPa','yield_strength':'YS_MPa','yield_0p2_MPa':'YS_MPa',
 'proof_strength_0p2_MPa':'YS_MPa','uts_MPa':'UTS_MPa','uts':'UTS_MPa',
 'ultimate_tensile_strength_MPa':'UTS_MPa','elongation':'elongation_pct','rupture_life':'rupture_hours',
 'CTE_linear_0_to_300C':'cte_0_300_ppm_K','density_g_per_cm3':'density_g_cm3'}
GOALS={'maximize':'max','minimize':'min','target':'target','max':'max','min':'min'}
def map_resolved_objectives(scope,allowed):
    raw=scope.get('objectives')
    if not isinstance(raw,dict) or not raw:raise ValueError('explicit resolved optimization objectives are required')
    output=[]
    for original,item in raw.items():
        key=ALIASES.get(original,original)
        if key not in allowed:raise ValueError('property is not predicted by this stack: '+original)
        if isinstance(item,(int,float)) and not isinstance(item,bool):
            if item<=0:raise ValueError('legacy objective weights must be positive')
            mode='max';extra={};scale=1/float(item)
        elif isinstance(item,dict):
            if set(item)-{'goal','target','scale','unit'}:raise ValueError('unknown goal fields')
            mode=GOALS.get(item.get('goal'));extra={'target':item['target']} if 'target' in item else {}
            scale=item.get('scale',1.)
            if 'unit' in item and item['unit']!=allowed[key]:raise ValueError('objective unit mismatch')
        else:raise ValueError('invalid objective definition')
        output.append(dict(property=key,mode=mode,scale=scale,unit=allowed[key],**extra))
    return output

def build_optimization_shadow(result):
    request=result.get('_optimization_request')
    if request is None:return {'items':[]},None
    return {'items':[request]},None
