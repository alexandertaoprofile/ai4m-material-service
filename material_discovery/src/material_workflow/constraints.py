"""Normalize direct and upstream-service material-discovery requests."""

from __future__ import annotations

import re
import hashlib
import uuid
from typing import Any, Dict, Iterable, Mapping, Tuple

from .schemas import GenerationConstraint

_ELEMENT = re.compile(r"^[A-Z][a-z]?$")
_ELEMENT_TOKEN = re.compile(r"[A-Z][a-z]?")
_CONTEXT_ELEMENT_TOKEN = re.compile(r"(?<![A-Za-z])[A-Z][a-z]?(?![a-z])")
_FORMULA = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\d*(?:\.\d*)?){2,}(?![A-Za-z0-9])")
_EXPLICIT_ELEMENT_SYSTEM = re.compile(r"(?<![A-Za-z])(?:[A-Z][a-z]?\s*(?:[-—–、，,]\s*[A-Z][a-z]?\s*)+)(?![a-z])")
_CURRENT_TASK_MARKER = re.compile(
    r"(?:接下来需要进行执行的任务|接下来执行的任务|当前(?:需要)?执行任务|执行任务)\s*[：:]\s*",
    flags=re.IGNORECASE,
)
_USER_TURN_MARKER = re.compile(r"(?:^|\n)用户\s*[：:]\s*", flags=re.IGNORECASE)
_ASSISTANT_TURN_MARKER = re.compile(r"(?:^|\n)助手\s*[：:]\s*", flags=re.IGNORECASE)
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


def normalize_taskid(value: Any) -> tuple[str, str]:
    """Return a safe local task key while retaining opaque gateway IDs in provenance.

    All filesystem-facing endpoints must use this function rather than placing
    a gateway-provided task ID directly in a local path. IDs that already use
    the established URL-safe form are left unchanged.
    """
    external_taskid = str(value or uuid.uuid4().hex).strip()
    if not external_taskid or len(external_taskid) > 512:
        raise ValueError("taskid must be a non-empty string no longer than 512 characters")
    if external_taskid in {".", ".."}:
        raise ValueError("taskid cannot be a path navigation segment")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", external_taskid):
        return external_taskid, external_taskid
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", external_taskid).strip("_.-")[:72]
    safe_taskid = f"{readable or 'new-material'}-{hashlib.sha256(external_taskid.encode('utf-8')).hexdigest()[:16]}"
    return safe_taskid, external_taskid


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
        # Never treat an acronym/legacy role identifier as a formula and then silently keep only the
        # element-looking suffix (N, S).  A conversational formula is usable
        # only when *every* parsed token is a real element symbol.
        if not (len(elements) >= 2 and all(element in _VALID_ELEMENTS for element in elements)):
            continue
        # Free text contains many all-uppercase workflow and polymer acronyms:
        # FDM/FFF, PLA, PETG, TPU, PDF, etc.  ``FFF`` is particularly harmful
        # because every F is a valid element token, so it used to become the
        # fictitious one-element system ``['F']``.  Accept a prose formula only
        # when it has at least two *different* elements and either a numerical
        # stoichiometry (Li3PS4, TiO2) or a normal mixed-case element symbol
        # (NaCl, LiLaTiPHO3).  Explicit element systems such as Li-P-S are
        # handled separately below.
        if len(set(elements)) < 2:
            continue
        has_stoichiometry = bool(re.search(r"\d", formula))
        has_mixed_case_symbol = bool(re.search(r"[A-Z][a-z]", formula))
        if has_stoichiometry or has_mixed_case_symbol:
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


def _fallback_context_text(payload: Mapping[str, Any], source: Mapping[str, Any]) -> str:
    """Return prior conversation only, never duplicating the current request.

    A follow-up such as ``继续执行`` contains no chemical system by itself.
    The caller must be able to recover the immediately preceding material
    constraints, while still giving an explicit current request precedence.
    """
    values = []
    for container in (source, payload):
        for key in ("conversation_context", "history", "messages", "previous_messages", "context"):
            value = _text(container.get(key))
            if value and value not in values:
                values.append(value)
    return "\n".join(values)


def _current_task_text(text: str) -> str:
    """Select the current execution instruction, not historic RAG prose.

    Upstream rounds prepend long summaries that may mention PLA/PETG, PDF,
    capitalised acronyms and unrelated material families.  Those are evidence
    context, not hard composition constraints.  The orchestrator's final
    ``接下来需要进行执行的任务: ...`` section is the authoritative natural-
    language source when no structured ``allowed_elements`` contract exists.
    """
    matches = list(_CURRENT_TASK_MARKER.finditer(text or ""))
    if matches:
        return (text or "")[matches[-1].end():].strip()

    # Some upstream callers provide a full chat transcript but omit the
    # orchestration marker.  In that shape, only the final user turn is the
    # request.  Do not scan the following assistant/RAG output: it may contain
    # incidental formulas such as SiC or BN that were merely cited in evidence.
    user_turns = list(_USER_TURN_MARKER.finditer(text or ""))
    if user_turns:
        current = (text or "")[user_turns[-1].end():]
        assistant = _ASSISTANT_TURN_MARKER.search(current)
        if assistant:
            current = current[:assistant.start()]
        return current.strip()
    return (text or "").strip()


def _elements_from_context(text: str) -> list[str]:
    formula_elements = _formula_elements(_formula_from_text(text))
    if len(formula_elements) >= 2:
        return list(dict.fromkeys(formula_elements))

    # Prefer an explicitly written system such as ``Nb-Mo-Ta-W`` over all
    # isolated element-looking tokens in a prose summary.  This prevents an
    # alloy designation such as CMSX-4 from donating a spurious carbon ``C``.
    for system in _EXPLICIT_ELEMENT_SYSTEM.findall(text):
        tokens = _ELEMENT_TOKEN.findall(system)
        if len(tokens) >= 2 and all(token in _VALID_ELEMENTS for token in tokens):
            return list(dict.fromkeys(tokens))

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

    Templates are starting points for generation, never claims of an
    optimized engineering composition. They are limited to supported inorganic
    crystal-discovery domains; a generic material request must still name its
    elements.
    """
    lower = text.lower()
    solid_electrolyte = "固态电解质" in text or "solid electrolyte" in lower
    sulfide_electrolyte = (
        "硫化物电解质" in text or "硫化物固态电解质" in text
        or "lgps" in lower or "li10gep2s12" in lower
    )
    garnet_electrolyte = "llzo" in lower or "石榴石电解质" in text
    if sulfide_electrolyte:
        return (
            "锂硫化物固态电解质",
            ["Li", "P", "S"],
            {"energy_above_hull": 0.05},
            {"ionic_conductivity": None},
        )
    if garnet_electrolyte:
        return (
            "石榴石型氧化物固态电解质",
            ["Li", "La", "Zr", "O"],
            {"energy_above_hull": 0.05},
            {"ionic_conductivity": None},
        )
    if solid_electrolyte:
        return (
            "锂基固态电解质探索",
            ["Li", "P", "S"],
            {"energy_above_hull": 0.05},
            {"ionic_conductivity": None},
        )
    return None


def _is_filament_only_request(text: str) -> bool:
    """Identify an FDM/FFF request that supplies no inorganic composition."""
    lower = text.lower()
    return any(term in lower for term in ("fdm", "fff", "filament")) or "丝材" in text


def _is_alloy_optimization_request(text: str) -> bool:
    """Keep alloy composition work out of the inorganic-crystal service."""
    lower = text.lower()
    return any(term in text for term in ("高熵合金", "难熔高熵", "合金配比", "元素比例", "原子百分比", "成分空间")) or any(
        term in lower for term in ("high entropy alloy", "refractory high entropy", " alloy ", "hea", "mpea")
    )


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
    conservatively recover an element system, an E_hull ceiling and qualitative
    validation goals from the current request and conversation context.  When
    the user has not supplied an E_hull ceiling, use the service's documented
    0.05 eV/atom default so MatterGen can select the corresponding conditional
    model instead of falling back to an unconditioned checkpoint.
    """
    source = _constraint_source(payload)
    taskid, _external_taskid = normalize_taskid(source.get("taskid") or payload.get("taskid") or uuid.uuid4().hex)
    raw_requirement = _text(source.get("raw_requirement") or payload.get("idea") or payload.get("instruction"))
    context = "\n".join(filter(None, [raw_requirement, _context_text(payload), _text(source.get("context"))]))
    # Do not mine the full multi-round transcript for atom symbols.  Prefer the
    # final orchestration instruction; it is the only free-text source allowed
    # to become a MatterGen chemical-system constraint.
    extraction_text = _current_task_text(raw_requirement or context)
    fallback_text = _current_task_text(_fallback_context_text(payload, source))
    if _is_alloy_optimization_request(extraction_text):
        raise ValueError(
            "当前请求属于合金成分/比例优化，不属于无机新晶体生成服务；"
            "请调用 alloy_composition_optimization 处理元素比例、原子百分比或成分空间设计。"
        )
    inferred_notes: list[str] = []
    target_formula = str(source.get("target_formula") or "").strip() or None
    candidate_formulas = source.get("candidate_formulas") or source.get("formulas") or []
    if not target_formula and isinstance(candidate_formulas, list) and candidate_formulas:
        target_formula = str(candidate_formulas[0]).strip() or None
    if not target_formula:
        target_formula = _formula_from_text(extraction_text)
    if not target_formula and fallback_text:
        target_formula = _formula_from_text(fallback_text)
    allowed = source.get("allowed_elements") or source.get("elements") or []
    if isinstance(allowed, str):
        allowed = [item for item in re.split(r"[-,，\s]+", allowed) if item]
    explicit_allowed = _elements(allowed)
    # A named domain template may supply conservative generation defaults even
    # if the element system itself was explicitly found in the request.
    domain_template = _domain_default(extraction_text)
    if not explicit_allowed:
        explicit_allowed = _elements_from_context(extraction_text)
        if explicit_allowed:
            inferred_notes.append(f"元素体系由当前需求自动提取：{'-'.join(explicit_allowed)}。")
    if not explicit_allowed and fallback_text:
        explicit_allowed = _elements_from_context(fallback_text)
        if explicit_allowed:
            inferred_notes.append(f"元素体系由上文任务自动恢复：{'-'.join(explicit_allowed)}。")
    if not explicit_allowed and target_formula:
        explicit_allowed = _formula_elements(target_formula)
        if explicit_allowed:
            inferred_notes.append(f"元素体系由化学式 {target_formula} 自动提取。")
    if not explicit_allowed:
        if domain_template:
            template_name, template_elements, _, _ = domain_template
            explicit_allowed = template_elements
            inferred_notes.append(
                f"已识别“{template_name}”材料方向，但任务中未给出可直接执行的精确化学式或元素集合；"
                f"暂以领域起始模板 {'-'.join(template_elements)} 作为探索起点，后续可确认或修改。"
            )
    if not explicit_allowed:
        if _is_filament_only_request(extraction_text):
            raise ValueError(
                "当前请求描述的是 FDM/FFF 丝材工艺，未提供可用于 MatterGen 的无机元素体系。"
                "FDM、FFF、PLA、PETG、TPU 等工艺或聚合物缩写不会作为化学式；"
                "请提供实际无机候选/填料的化学式或元素组合，例如 SiC、B4C、Al-N 或 Li-P-S。"
            )
        raise ValueError(
            "无法确定待生成的元素体系。请提供化学式、元素组合或材料类别，例如“Li-P-S 硫化物固态电解质”、"
            "“Li-La-Zr-O 石榴石电解质”或“Ti-O 氧化物光催化剂”。"
        )

    properties = _numeric_mapping(source.get("target_properties"), "target_properties")
    if "energy_above_hull" not in properties:
        inferred_ehull = _ehull_from_context(extraction_text)
        if inferred_ehull is None:
            inferred_ehull = _ehull_from_context(fallback_text)
        if inferred_ehull is not None:
            properties["energy_above_hull"] = inferred_ehull
            inferred_notes.append(f"稳定性阈值由需求文本自动提取：E_hull ≤ {inferred_ehull:g} eV/atom。")
        elif domain_template:
            properties.update(domain_template[2])
            inferred_notes.append("稳定性生成引导采用领域模板默认值：E_hull ≤ 0.05 eV/atom。")
        else:
            # The cached and deployed MatterGen checkpoint is conditioned on
            # chemical system plus E_hull.  A conservative default avoids
            # switching an otherwise well-defined request to the separately
            # cached/unavailable chemical_system-only checkpoint merely because
            # the user did not state a numerical threshold.
            properties["energy_above_hull"] = 0.05
            inferred_notes.append("未给出稳定性阈值，采用默认生成引导：E_hull ≤ 0.05 eV/atom。")
    validation_targets = _numeric_mapping(source.get("validation_targets"), "validation_targets", allow_none=True)
    if not validation_targets:
        validation_targets = _validation_targets_from_context(extraction_text) or _validation_targets_from_context(fallback_text)
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
        "external_taskid": str(payload.get("taskid") or constraint.taskid),
        "safe_taskid": constraint.taskid,
        "user_name": str(payload.get("user_name") or ""),
        "has_file_metadata": bool(payload.get("file_metadata")),
        "upstream_contract": next((key for key in _CONSTRAINT_KEYS if isinstance(payload.get(key), Mapping)), "legacy_envelope"),
    }
