"""Deterministic current-request requirements, separate from historic context."""
from __future__ import annotations

import math
import re


PROPERTY_NAMES = {
    "band_gap": r"带隙|禁带宽度|band[ _-]?gap",
    "bulk_modulus": r"体积模量|bulk[ _-]?modulus",
    "shear_modulus": r"剪切模量|shear[ _-]?modulus",
    "dielectric_constant_x": r"(?:x\s*方向\s*)?介电常数|dielectric[ _-]?constant(?:[ _-]?x)?",
    "electron_effective_mass": r"电子有效质量|electron[ _-]?effective[ _-]?mass",
    "hole_effective_mass": r"空穴有效质量|hole[ _-]?effective[ _-]?mass",
}
PROPERTY_UNITS = {"band_gap": "eV", "bulk_modulus": "GPa", "shear_modulus": "GPa",
                  "dielectric_constant_x": "1", "electron_effective_mass": "m0", "hole_effective_mass": "m0"}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
COMPARATOR = r"不低于|不少于|不小于|至少|大于等于|>=|≥|不超过|不高于|不大于|至多|最多|小于等于|<=|≤|大于|高于|超过|>|小于|低于|<"
LOWER = {"不低于", "不少于", "不小于", "至少", "大于等于", ">=", "≥", "大于", "高于", "超过", ">"}
STRICT = {"大于", "高于", "超过", ">", "小于", "低于", "<"}
UNITS = r"meV|eV|TPa|GPa|MPa|kPa|Pa|m0|无量纲|1"


def normalized_text(text):
    text = str(text).translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789"))
    for before, after in ((r"\geqslant", "≥"), (r"\leqslant", "≤"), (r"\geq", "≥"), (r"\leq", "≤"), (r"\ge", "≥"), (r"\le", "≤")):
        text = text.replace(before, after)
    return text.replace("$", "").replace("\\,", " ")


def _quantity(key, value, unit):
    number = float(value)
    unit = (unit or "").casefold()
    scales = ({"ev": 1, "mev": .001} if key == "band_gap" else
              {"gpa": 1, "mpa": .001, "tpa": 1000, "kpa": .000001, "pa": .000000001} if key in {"bulk_modulus", "shear_modulus"} else
              {"": 1, "1": 1, "无量纲": 1} if key == "dielectric_constant_x" else {"m0": 1})
    if unit not in scales:
        label = PROPERTY_NAMES[key].split("|")[0]
        label = "介电常数" if key == "dielectric_constant_x" else label
        raise ValueError(f"请明确{label}的数值单位（{PROPERTY_UNITS[key]}），再确认计算条件。")
    number *= scales[unit]
    if not math.isfinite(number):
        raise ValueError("性质条件需要有限数值。")
    return number


def _put(bound, side, value, inclusive=True):
    old = float(bound[side]) if side in bound else None
    if old is None or (side == "min" and value > old) or (side == "max" and value < old):
        bound[side] = value
        bound.pop(side + "_inclusive", None)
        if not inclusive:
            bound[side + "_inclusive"] = False
    elif value == old and not inclusive:
        bound[side + "_inclusive"] = False


def explicit_property_bounds(text):
    """Parse stated bounds/units; qualitative desires remain comparison goals."""
    text = normalized_text(text)
    result = {}
    names = "|".join(f"(?:{pattern})" for pattern in PROPERTY_NAMES.values())
    occurrences = list(re.finditer(names, text, re.I))
    for index, occurrence in enumerate(occurrences):
        key = next(key for key, pattern in PROPERTY_NAMES.items() if re.fullmatch(pattern, occurrence.group(), re.I))
        end = occurrences[index + 1].start() if index + 1 < len(occurrences) else len(text)
        clause = re.split(r"[。；;\n]", text[occurrence.end():end], maxsplit=1)[0][:180]
        before = text[max(0, occurrence.start() - 12):occurrence.start()]
        if re.search(r"无需限定|不要求|不限制|取消(?:对)?$", before) or re.match(r"\s*(?:不要求|不限制|不限|无需限定)", clause):
            continue
        bound = {"unit": PROPERTY_UNITS[key]}
        interval = re.search(rf"(?:在|为|范围(?:为|是)?|介于)?\s*({NUMBER})\s*({UNITS})?\s*(?:[~～—–-]|至|到)\s*({NUMBER})\s*({UNITS})?", clause, re.I)
        if interval and interval.start() < 18 and not re.search(r"温度|压力|时间|原子数|候选", clause[:interval.start()]):
            first_unit, second_unit = interval.group(2), interval.group(4)
            _put(bound, "min", _quantity(key, interval.group(1), first_unit or second_unit))
            _put(bound, "max", _quantity(key, interval.group(3), second_unit or first_unit))
        previous_end = None
        matches = list(re.finditer(rf"({COMPARATOR})\s*({NUMBER})\s*({UNITS})?", clause, re.I))
        for match_index, match in enumerate(matches):
            gap = clause[previous_end or 0:match.start()]
            if previous_end is None:
                if match.start() > 18 or re.search(r"温度|压力|时间|原子数|候选", gap):
                    continue
            elif not re.fullmatch(r"(?:\s|[，,、]|且|并且|同时|并|和|及|以及)*", gap):
                continue
            comparator = match.group(1)
            unit = match.group(3)
            if not unit and match_index + 1 < len(matches):
                following = matches[match_index + 1]
                if re.fullmatch(r"(?:\s|[，,、]|且|并且|同时|并|和|及|以及)*", clause[match.end():following.start()]):
                    unit = following.group(3)
            _put(bound, "min" if comparator in LOWER else "max", _quantity(key, match.group(2), unit), comparator not in STRICT)
            previous_end = match.end()
        if "min" not in bound and "max" not in bound:
            continue
        existing = result.setdefault(key, {"unit": PROPERTY_UNITS[key]})
        for side in ("min", "max"):
            if side in bound:
                _put(existing, side, bound[side], bound.get(side + "_inclusive", True))
        low, high = existing.get("min", -math.inf), existing.get("max", math.inf)
        if low > high or low == high and (existing.get("min_inclusive") is False or existing.get("max_inclusive") is False):
            raise ValueError(f"{key}的上下限冲突，请确认希望采用的范围。")
    return result


def explicit_elements(text):
    """Only an explicit inclusion/exclusion phrase contributes element rules."""
    from .constraints import _CHINESE_ELEMENTS, _VALID_ELEMENTS
    from .omatg_chart import ELEMENT_NAMES
    chinese_names = {**_CHINESE_ELEMENTS, **{name: symbol for symbol, name in ELEMENT_NAMES.items()}, "铅": "Pb"}
    text = normalized_text(text)
    symbol = r"[A-Z][a-z]?(?![a-zA-Z])"
    chinese = "|".join(sorted(map(re.escape, chinese_names), key=len, reverse=True))
    item = rf"(?:{symbol}|{chinese})"
    pattern = rf"(?P<action>必须包含|必须含有|必须含|必含元素|必含|不得包含|禁止包含|禁止使用|禁止元素|排除元素|排除)\s*(?:的?元素)?\s*[：:]?\s*(?P<items>{item}(?:\s*[,，、/\-与和及\s]\s*{item})*)(?=$|[\s，,；;。：:])"
    required, excluded = [], []
    for match in re.finditer(pattern, text):
        before = text[max(0, match.start() - 12):match.start()]
        if re.search(r"不要求|无需|不必|不需要", before):
            continue
        symbols = []
        for token in re.findall(item, match.group("items")):
            value = chinese_names.get(token, token)
            if value not in _VALID_ELEMENTS:
                raise ValueError(f"请确认元素符号 {value}。")
            if value not in symbols:
                symbols.append(value)
        target = required if match.group("action") in {"必须包含", "必须含有", "必须含", "必含元素", "必含"} else excluded
        target.extend(value for value in symbols if value not in target)
    return required, excluded


def merge_property_bounds(structured, parsed):
    """Keep both explicit sources; contradictory conditions ask for correction."""
    result = {key: dict(value) if isinstance(value, dict) else value for key, value in structured.items()}
    for key, bound in parsed.items():
        if key not in result:
            result[key] = dict(bound)
            continue
        target = result[key]
        if not isinstance(target, dict):
            continue  # The contract validator reports the malformed field.
        for side in ("min", "max"):
            if side in bound:
                _put(target, side, bound[side], bound.get(side + "_inclusive", True))
        target.setdefault("unit", bound["unit"])
    return result
