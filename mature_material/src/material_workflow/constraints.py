"""Normalize direct and upstream-service material-discovery requests."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, Mapping, Tuple

from .schemas import GenerationConstraint

_ELEMENT = re.compile(r"^[A-Z][a-z]?$")
_ELEMENT_TOKEN = re.compile(r"[A-Z][a-z]?")
_CONTEXT_ELEMENT_TOKEN = re.compile(r"(?<![A-Za-z])[A-Z][a-z]?(?![a-z])")
_FORMULA = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\d*(?:\.\d*)?){2,}(?![A-Za-z0-9])")
_CONSTRAINT_KEYS = ("new_material", "mattergen", "generation_constraints", "constraints")
_VALID_ELEMENTS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr
Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu
Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og
""".split())
_CHINESE_ELEMENTS = {
    "铌": "Nb", "钼": "Mo", "钽": "Ta", "钨": "W", "铬": "Cr", "钴": "Co", "镍": "Ni", "铁": "Fe",
    "铝": "Al", "钛": "Ti", "锆": "Zr", "铪": "Hf", "钒": "V", "锰": "Mn", "铜": "Cu", "硅": "Si",
    "硫": "S", "磷": "P", "锂": "Li", "钠": "Na", "钾": "K", "镁": "Mg", "钙": "Ca",
}
_VALIDATION_LABELS = {
    "high_temperature_strength": "高温强度", "creep_resistance": "抗蠕变能力",
    "oxidation_resistance": "抗氧化能力", "thermal_fatigue": "热疲劳抗力",
    "additive_manufacturability": "增材制造适配性", "ionic_conductivity": "离子电导率",
    "band_gap": "带隙",
}


def _validation_labels(values: Iterable[str]) -> str:
    return "、".join(_VALIDATION_LABELS.get(value, value.replace("_", " ")) for value in values)


def _elements(values: Iterable[Any]) -> list[str]:
    result = []
    for value in values:
        element = str(value).strip().capitalize()
        if not _ELEMENT.fullmatch(element):
            raise ValueError(f"Invalid element symbol: {value!r}")
        if element not in result:
            result.append(element)
    return result


def _numeric_mapping(value: Any, field_name: str, *, allow_none: bool = False) -> Dict[str, float | None]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    result: Dict[str, float] = {}
    for name, item in value.items():
        if item is None and allow_none:
            result[str(name)] = None
            continue
        if isinstance(item, bool):
            raise ValueError(f"{field_name}.{name!r} must be numeric")
        try:
            result[str(name)] = float(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}.{name!r} must be numeric") from exc
    return result


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("idea") or value.get("content") or value.get("text") or value.get("query") or "")
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value if _text(item))
    return str(value or "")


def _formula_from_text(text: str) -> str | None:
    for formula in _FORMULA.findall(text):
        elements = _ELEMENT_TOKEN.findall(re.sub(r"\d+(?:\.\d+)?", "", formula))
        if len(elements) >= 2:
            return formula
    return None


def _formula_elements(formula: str | None) -> list[str]:
    return [token for token in _ELEMENT_TOKEN.findall(formula or "") if token in _VALID_ELEMENTS]


def _context_text(payload: Mapping[str, Any]) -> str:
    """Collect conversational context without requiring a frontend JSON schema."""
    values = []
    for key in ("idea", "instruction", "raw_requirement", "context", "conversation_context", "history", "messages", "previous_messages"):
        value = payload.get(key)
        if value:
            values.append(_text(value))
    # File metadata often contains a concise upstream abstract/title.  It may
    # help extraction, but it never overrides explicit material constraints.
    if payload.get("file_metadata"):
        values.append(_text(payload.get("file_metadata")))
    return "\n".join(value for value in values if value.strip())


def _elements_from_context(text: str) -> list[str]:
    formula_elements = _formula_elements(_formula_from_text(text))
    if len(formula_elements) >= 2:
        return list(dict.fromkeys(formula_elements))

    # Covers Nb-Mo-Ta-W, Nb、Mo、Ta、W and "Nb Mo Ta W".  Filter against
    # the periodic table to avoid treating abbreviations such as DFT as atoms.
    ascii_elements = [token for token in _CONTEXT_ELEMENT_TOKEN.findall(text) if token in _VALID_ELEMENTS]
    if len(ascii_elements) >= 2:
        return list(dict.fromkeys(ascii_elements))

    # Chinese symbols are deliberately accepted only when at least two are
    # present, so words such as "抗氧化" cannot accidentally add oxygen.
    chinese_elements = [symbol for name, symbol in _CHINESE_ELEMENTS.items() if name in text]
    if len(chinese_elements) >= 2:
        return list(dict.fromkeys(chinese_elements))
    return []


def _ehull_from_context(text: str) -> float | None:
    """Extract an explicit E_hull ceiling and normalize meV/atom to eV/atom."""
    property_name = r"(?:e\s*[_-]?\s*hull|energy\s+above\s+hull|高于凸包(?:能)?)"
    number = r"([0-9]+(?:\.[0-9]+)?)\s*(mev|ev)?\s*(?:/\s*atom)?"
    patterns = (
        rf"{property_name}.{{0,40}}?(?:≤|<=|<|不超过|低于|小于|以内)\s*{number}",
        rf"{number}.{{0,20}}?(?:以下|以内|不超过|低于|小于)?\s*(?:的)?\s*{property_name}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value, unit = match.group(1), (match.group(2) or "ev").lower()
        result = float(value)
        return result / 1000 if unit == "mev" else result
    return None


def _validation_targets_from_context(text: str) -> Dict[str, None]:
    lower = text.lower()
    targets: Dict[str, None] = {}
    if any(term in text for term in ("高温", "难熔")) or "refractory" in lower or "high-temperature" in lower:
        targets["high_temperature_strength"] = None
    if "蠕变" in text or "creep" in lower:
        targets["creep_resistance"] = None
    if "抗氧化" in text or "氧化阻力" in text or "oxidation" in lower:
        targets["oxidation_resistance"] = None
    if "离子电导" in text or "ionic conductivity" in lower:
        targets["ionic_conductivity"] = None
    if "带隙" in text or "band gap" in lower:
        targets["band_gap"] = None
    return targets


def _domain_default(text: str) -> tuple[str, list[str], Dict[str, float], Dict[str, None]] | None:
    """Return an auditable starting template for a clearly named use case.

    This is intentionally narrow: a generic "alloy" request must still name
    its elements.  Templates are starting points for generation, never claims
    of an optimized engineering composition.
    """
    lower = text.lower()
    additive_request = any(term in text for term in ("3d打印", "3D打印", "增材制造")) or "additive manufacturing" in lower
    rocket_request = any(term in text for term in ("爆震", "火箭", "发动机")) or "detonation" in lower or "rocket engine" in lower
    additive_rocket = additive_request and rocket_request
    if additive_rocket:
        return (
            "金属增材制造爆震/火箭发动机高温合金",
            ["Ni", "Co", "Cr", "Al", "Ti"],
            {"energy_above_hull": 0.05},
            {
                "high_temperature_strength": None,
                "creep_resistance": None,
                "oxidation_resistance": None,
                "thermal_fatigue": None,
                "additive_manufacturability": None,
            },
        )
    high_entropy = any(term in text for term in ("高温高熵", "难熔高熵", "高熵合金")) or "refractory high entropy" in lower or "high entropy alloy" in lower
    if high_entropy:
        return (
            "难熔高熵合金",
            ["Nb", "Mo", "Ta", "W"],
            {"energy_above_hull": 0.05},
            {
                "high_temperature_strength": None,
                "creep_resistance": None,
                "oxidation_resistance": None,
            },
        )
    return None


def _constraint_source(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge a standard upstream envelope with its optional explicit contract."""
    source = dict(payload)
    for key in _CONSTRAINT_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            source.update(nested)
            break
    if not source.get("target_properties") and isinstance(source.get("property_targets"), Mapping):
        source["target_properties"] = source["property_targets"]
    if not source.get("validation_targets") and isinstance(source.get("validation_requirements"), Mapping):
        source["validation_targets"] = source["validation_requirements"]
    return source


def constraint_from_payload(payload: Dict[str, Any]) -> GenerationConstraint:
    """Convert a direct HTTP or existing ``/start`` envelope into constraints.

    Explicit ``constraints`` win. When they are absent, the service can
    conservatively recover an element system, explicit E_hull ceiling and
    qualitative validation goals from the current request and conversation
    context. It never invents a numeric target property.
    """
    source = _constraint_source(payload)
    taskid = str(source.get("taskid") or payload.get("taskid") or uuid.uuid4().hex).strip()
    if not taskid or taskid in {".", ".."} or "/" in taskid or "\\" in taskid:
        raise ValueError("taskid must be a non-empty identifier without path separators")
    raw_requirement = _text(source.get("raw_requirement") or payload.get("idea") or payload.get("instruction"))
    context = "\n".join(filter(None, [raw_requirement, _context_text(payload), _text(source.get("context"))]))
    inferred_notes: list[str] = []
    target_formula = str(source.get("target_formula") or "").strip() or None
    candidate_formulas = source.get("candidate_formulas") or source.get("formulas") or []
    if not target_formula and isinstance(candidate_formulas, list) and candidate_formulas:
        target_formula = str(candidate_formulas[0]).strip() or None
    if not target_formula:
        target_formula = _formula_from_text(raw_requirement) or _formula_from_text(context)
    allowed = source.get("allowed_elements") or source.get("elements") or []
    if isinstance(allowed, str):
        allowed = [item for item in re.split(r"[-,，\s]+", allowed) if item]
    explicit_allowed = _elements(allowed)
    if not explicit_allowed:
        explicit_allowed = _elements_from_context(raw_requirement) or _elements_from_context(context)
        if explicit_allowed:
            inferred_notes.append(f"元素体系由当前需求/上文自动提取：{'-'.join(explicit_allowed)}。")
    if not explicit_allowed and target_formula:
        explicit_allowed = _formula_elements(target_formula)
        if explicit_allowed:
            inferred_notes.append(f"元素体系由化学式 {target_formula} 自动提取。")
    domain_template = None
    if not explicit_allowed:
        domain_template = _domain_default(raw_requirement) or _domain_default(context)
        if domain_template:
            template_name, template_elements, _, _ = domain_template
            explicit_allowed = template_elements
            inferred_notes.append(
                f"未指定元素体系，已采用“{template_name}”领域起始模板：{'-'.join(template_elements)}；请在生成前确认或修改。"
            )
    if not explicit_allowed:
        raise ValueError("无法从当前需求或上文确定元素体系；请说明元素组合，例如“Nb-Mo-Ta-W 难熔合金”。")

    properties = _numeric_mapping(source.get("target_properties"), "target_properties")
    if "energy_above_hull" not in properties:
        inferred_ehull = _ehull_from_context(raw_requirement)
        if inferred_ehull is None:
            inferred_ehull = _ehull_from_context(context)
        if inferred_ehull is not None:
            properties["energy_above_hull"] = inferred_ehull
            inferred_notes.append(f"稳定性阈值由需求文本自动提取：E_hull ≤ {inferred_ehull:g} eV/atom。")
        elif domain_template:
            properties.update(domain_template[2])
            inferred_notes.append("稳定性生成引导采用领域模板默认值：E_hull ≤ 0.05 eV/atom。")

    validation_targets = _numeric_mapping(source.get("validation_targets"), "validation_targets", allow_none=True)
    if not validation_targets:
        validation_targets = _validation_targets_from_context(raw_requirement) or _validation_targets_from_context(context)
        if validation_targets:
            inferred_notes.append("验证关注点由需求文本自动提取：" + _validation_labels(validation_targets) + "。")
        if domain_template:
            template_targets = domain_template[3]
            missing_template_targets = [name for name in template_targets if name not in validation_targets]
            validation_targets = {**template_targets, **validation_targets}
            if missing_template_targets:
                inferred_notes.append("补充采用领域模板验证关注点：" + _validation_labels(missing_template_targets) + "；这些是后续验证项目，并非本轮已验证性能。")

    return GenerationConstraint(
        taskid=taskid,
        raw_requirement=raw_requirement,
        target_formula=target_formula,
        allowed_elements=explicit_allowed,
        excluded_elements=_elements(source.get("excluded_elements") or source.get("forbidden_elements") or []),
        target_properties=properties,
        validation_targets=validation_targets,
        notes=[str(note) for note in (source.get("notes") or [])] + inferred_notes,
    )


def upstream_contract(payload: Mapping[str, Any]) -> Tuple[GenerationConstraint, Dict[str, Any]]:
    """Return normalized constraints plus request provenance safe for manifests."""
    constraint = constraint_from_payload(dict(payload))
    return constraint, {
        "user_name": str(payload.get("user_name") or ""),
        "has_file_metadata": bool(payload.get("file_metadata")),
        "upstream_contract": next((key for key in _CONSTRAINT_KEYS if isinstance(payload.get(key), Mapping)), "legacy_envelope"),
    }
