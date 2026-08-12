"""Shared vocabulary for material-service request properties.

This is an interpretation registry, not a claim that every property has been
ingested.  A recognised property without catalogue evidence must remain
``missing`` during screening.
"""
from __future__ import annotations


# property, Chinese display label, aliases, accepted numeric-unit regex, canonical unit
PROPERTY_VOCABULARY = (
    ("density", "密度", ("密度", "比重"), r"(?:kg\s*/\s*m(?:\^?3|³)|g\s*/\s*cm(?:\^?3|³))", "kg/m³"),
    ("ultimate_tensile_strength", "抗拉强度", ("抗拉强度", "极限抗拉强度", "拉伸强度", "UTS"), r"MPa", "MPa"),
    ("yield_strength", "屈服强度", ("屈服强度", "屈服应力", "YS"), r"MPa", "MPa"),
    ("compressive_strength", "压缩强度", ("压缩强度", "抗压强度"), r"MPa", "MPa"),
    ("flexural_strength", "弯曲强度", ("弯曲强度", "抗弯强度"), r"MPa", "MPa"),
    ("shear_strength", "剪切强度", ("剪切强度", "抗剪强度", "层间剪切强度"), r"MPa", "MPa"),
    ("interfacial_bond_strength", "界面结合力", ("界面结合力", "层间结合力", "结合力", "粘接强度"), r"MPa", "MPa"),
    ("youngs_modulus", "杨氏模量", ("杨氏模量", "弹性模量", "拉伸模量", "E模量"), r"GPa", "GPa"),
    ("shear_modulus", "剪切模量", ("剪切模量", "G模量"), r"GPa", "GPa"),
    ("hardness", "硬度", ("硬度", "维氏硬度", "洛氏硬度", "布氏硬度"), r"(?:HV|HBW?|HRC|HRB)", ""),
    ("elongation", "延伸率", ("延伸率", "断裂伸长率", "断裂延伸率"), r"%", "%"),
    ("thermal_conductivity", "导热系数", ("导热系数", "导热率", "导热"), r"W\s*/\s*\(?\s*m\s*[·*.]?\s*K\s*\)?", "W/(m·K)"),
    ("specific_heat", "比热容", ("比热容", "比热"), r"J\s*/\s*\(?\s*kg\s*[·*.]?\s*K\s*\)?", "J/(kg·K)"),
    ("thermal_diffusivity", "热扩散率", ("热扩散率",), r"(?:mm(?:\^?2|²)\s*/\s*s|m(?:\^?2|²)\s*/\s*s)", ""),
    ("thermal_expansion_coefficient", "热膨胀系数", ("热膨胀系数", "线膨胀系数", "CTE"), r"(?:ppm\s*/\s*K|10\^?-?6\s*/\s*K)", "ppm/K"),
    ("heat_deflection_temperature", "热变形温度", ("热变形温度", "HDT"), r"K", "K"),
    ("glass_transition_temperature", "玻璃化转变温度", ("玻璃化转变温度", "玻璃化温度", "Tg"), r"K", "K"),
    ("electrical_resistivity", "电阻率", ("电阻率", "体积电阻率"), r"(?:Ω|ohm)\s*[·*.]?\s*m", "Ω·m"),
    ("electrical_conductivity", "电导率", ("电导率", "导电率"), r"S\s*/\s*m", "S/m"),
    ("dielectric_strength", "介电强度", ("介电强度", "击穿强度"), r"kV\s*/\s*mm", "kV/mm"),
    ("dielectric_constant", "介电常数", ("介电常数", "相对介电常数", "介电率"), r"", ""),
    ("water_absorption", "吸水率", ("吸水率", "吸湿率"), r"%", "%"),
    ("surface_roughness_ra", "表面粗糙度 Ra", ("表面粗糙度", "粗糙度", "Ra"), r"(?:μm|um)", "μm"),
    ("material_cost", "单位质量成本", ("单位质量成本", "材料成本", "成本"), r"(?:USD|\$)\s*/\s*kg", "USD/kg"),
    ("fatigue_strength", "疲劳强度", ("疲劳强度", "疲劳极限"), r"MPa", "MPa"),
)


def vocabulary_aliases() -> dict[str, str]:
    """Return raw aliases; caller applies its own text-normalisation policy."""
    return {alias: property_name for property_name, _label, aliases, _unit_pattern, _unit in PROPERTY_VOCABULARY for alias in aliases}


def vocabulary_labels() -> dict[str, str]:
    return {property_name: label for property_name, label, _aliases, _unit_pattern, _unit in PROPERTY_VOCABULARY}
