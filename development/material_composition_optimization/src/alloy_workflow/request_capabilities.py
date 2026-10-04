"""Keep unavailable property requirements separate from executable objectives."""
from copy import deepcopy
import json

ALIASES={'yield_strength':'YS_MPa','yield_strength_MPa':'YS_MPa','yield_0p2_MPa':'YS_MPa',
    'proof_strength_0p2_MPa':'YS_MPa','uts':'UTS_MPa','uts_MPa':'UTS_MPa',
    'ultimate_tensile_strength_MPa':'UTS_MPa','elongation':'elongation_pct',
    'rupture_life':'rupture_hours','CTE_linear_0_to_300C':'cte_0_300_ppm_K',
    'density_g_per_cm3':'density_g_cm3'}
FIELDS={
    'hea_mpea':{'YS_MPa','UTS_MPa','phase_risk'},
    'reusable_rocket_stainless':{'YS_MPa','UTS_MPa','elongation_pct'},
    'aerospace_aluminum_composition_v1':{'YS_MPa','UTS_MPa','elongation_pct'},
    'ni_superalloy_hot_end':{'YS_MPa','UTS_MPa','elongation_pct','rupture_hours'},
    'copper_hot_end_local_composition_v1':{'YS_MPa','UTS_MPa'},
    'chip_glass_thermomechanical_family_v1':{'density_g_cm3','young_modulus_GPa','cte_0_300_ppm_K',
        'stress_optical_coefficient_nm_cm_per_MPa','viscosity_temperature_200_poise_C','viscosity_temperature_35kpoise_C'},
    'short_cf_thermomechanical_rve_v1':{'E11_MPa','E22_MPa','E33_MPa','G12_MPa','G13_MPa','G23_MPa',
        'V12','V13','V23','fiber_volume_fraction','anisotropy_ratio_E11_E22'},
    'perovskite_transport_stability_v2':{'ln_sigmaT_220K','ln_sigmaT_300K'},
}
GATES={'uts_min_MPa':'UTS_MPa','proof_strength_min_MPa':'YS_MPa','rupture_life_min_h':'rupture_hours',
    'CTE_linear_0_to_300C_max_ppm_per_K':'cte_0_300_ppm_K','density_g_per_cm3_max':'density_g_cm3',
    'young_modulus_GPa_min':'young_modulus_GPa','stress_optical_coefficient_nm_cm_per_MPa_max':'stress_optical_coefficient_nm_cm_per_MPa'}


def separate_requirements(scope, decisions):
    allowed=FIELDS.get(scope.get('model_domain'))
    if allowed is None:return
    removed={}
    for field in ('objectives','screening_thresholds'):
        if not isinstance(scope.get(field),dict):continue
        for key,value in list(scope[field].items()):
            prop=GATES.get(key) if field=='screening_thresholds' else ALIASES.get(key,key)
            if prop in allowed:continue
            removed.setdefault(field,{})[key]=deepcopy(value)
            del scope[field][key]
            detail=key+'：'+json.dumps(value,ensure_ascii=False)
            scope.setdefault('_uncomputed_requirements',[]).append(detail)
            decisions.append({'field':'uncomputed_requirements','parameter':'尚未计算的要求',
                'adopted':'保留原要求，其余已支持性质继续优化','reason':detail+'；当前专项优化器未支持此项。'})
    if removed:scope['_uncomputed_structured_requirements']=removed
