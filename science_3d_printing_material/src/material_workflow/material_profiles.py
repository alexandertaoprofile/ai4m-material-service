"""Display profiles for material formulas used by websocket summaries."""

from __future__ import annotations

import re


def formula_profile(formula: str) -> dict:
    """Return a conservative display profile for a material formula or family."""
    f = str(formula or "").strip()
    f_up = f.upper()
    f_low = f.lower()

    def looks_formula_local(value: str) -> bool:
        text = str(value or "").strip()
        return bool(re.fullmatch(r"(?:[A-Z][a-z]?\d*){2,}", text))

    if f_up in {"LLZO", "LI7LA3ZR2O12"}:
        return {
            "中文名称": "石榴石型氧化物固态电解质（LLZO）",
            "材料类别": "氧化物固态电解质",
            "应用角色": "锂离子导体骨架相",
        }

    if f_up in {"PEO", "P(EO)", "POLYETHYLENE OXIDE"}:
        return {
            "中文名称": "聚氧化乙烯（PEO）",
            "材料类别": "聚合物电解质基体",
            "应用角色": "离子传导聚合物相",
        }

    if "-" in f and len(f.split("-")) == 2:
        a, b = [x.strip() for x in f.split("-", 1)]
        au, bu = a.upper(), b.upper()
        if {au, bu} == {"LLZO", "PEO"}:
            return {
                "中文名称": "LLZO-PEO 复合固态电解质",
                "材料类别": "无机-聚合物复合电解质",
                "应用角色": "复合电解质候选相",
            }
        if looks_formula_local(a) and looks_formula_local(b):
            return {
                "中文名称": f"{a}-{b} 二元材料体系",
                "材料类别": "二元无机材料体系",
                "应用角色": "成分协同筛选体系",
            }

    if f_up in {"AL2O3"}:
        return {
            "中文名称": "氧化铝（Al2O3）",
            "材料类别": "氧化物陶瓷",
            "应用角色": "机械增强/绝缘稳定相",
        }

    if f_up in {"LI3N"}:
        return {
            "中文名称": "氮化锂（Li3N）",
            "材料类别": "无机锂离子导体",
            "应用角色": "高锂离子传导候选相",
        }

    if ("li" in f_low and "s" in f_low and "p" in f_low) or f_up in {"LI6PS5CL", "LI3PS4", "LPSCL"}:
        return {
            "中文名称": "锂-磷-硫体系固态电解质候选",
            "材料类别": "硫化物固态电解质",
            "应用角色": "锂离子传导相/电解质相",
        }

    return {
        "中文名称": "无机化合物候选",
        "材料类别": "无机功能材料",
        "应用角色": "待筛选候选相",
    }
