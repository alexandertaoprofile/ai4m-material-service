"""Conservative LLM assistance for underspecified material-discovery requests.

The model is used only to turn the already received task/context into a small
element-system proposal.  It is not allowed to invent a catalogue result,
material property, or a precise composition; every returned element is
validated locally before it is added to the normal constraint contract.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

logger = logging.getLogger("mattergen_workflow")
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_MARKER = re.compile(r"(?:接下来需要进行执行的任务|接下来执行的任务|当前(?:需要)?执行任务|执行任务)\s*[：:]\s*", re.I)
_VALID_ELEMENTS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr
Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu
Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og
""".split())


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(_text(value.get(key)) for key in (
            "idea", "content", "text", "query", "summary", "message", "requirement", "history", "messages",
        ) if value.get(key) is not None)
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    return str(value or "")


def _context(payload: Mapping[str, Any]) -> tuple[str, str]:
    current = _text(payload.get("idea") or payload.get("instruction") or payload.get("raw_requirement"))
    all_text = "\n".join(_text(payload.get(key)) for key in (
        "idea", "instruction", "raw_requirement", "context", "conversation_context", "history", "messages", "previous_messages",
    ) if payload.get(key) is not None)
    matches = list(_MARKER.finditer(all_text))
    if matches:
        current = all_text[matches[-1].end():].strip()
    return current[:2400], all_text[:9000]


def _config() -> dict[str, Any]:
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


async def infer_element_system(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a validated, explainable element-system proposal or ``None``.

    It deliberately runs only after deterministic extraction failed.  The
    current instruction has precedence; the upstream evidence is supporting
    context rather than an instruction to blindly copy every formula it cites.
    """
    if os.getenv("NEW_MATERIAL_LLM_ELEMENT_INFERENCE", "true").lower() not in {"1", "true", "yes"}:
        return None
    config = _config()
    base_url = os.getenv("NEW_MATERIAL_LLM_BASE_URL") or config.get("base_url_1")
    api_key = os.getenv("NEW_MATERIAL_LLM_API_KEY") or config.get("api_key")
    if not base_url or not api_key:
        logger.warning("[CONSTRAINT_LLM] unavailable: LLM configuration is missing")
        return None
    current, evidence = _context(payload)
    prompt = (
        "你是无机新材料生成流程的约束解析器。当前请求缺少显式化学式或元素组合。"
        "请根据当前执行任务和已接收的上游材料结论，判断能否提出一个用于‘起始探索’的元素体系。"
        "当前执行任务优先；上游文本仅可作为与当前任务一致时的证据。\n\n"
        "只输出 JSON：{\"allowed_elements\":[\"Li\",\"P\",\"S\"],\"material_family\":\"…\",\"reason\":\"不超过80字\",\"confidence\":\"high|medium|low\"}。\n"
        "规则：\n"
        "1. 只可输出有效元素符号，2–8 个；不能输出材料名、缩写、商品名或猜测的比例。\n"
        "2. 若无法从文本获得有依据的体系，返回 allowed_elements 空数组。\n"
        "3. 例如文本明确讨论硫化物固态电解质及 Li-P-S 时，可给 Li/P/S；仅有‘环保电池’不能凭空选择元素。\n"
        "4. 不要声称已经计算、检索数据库或验证性能。\n\n"
        f"当前执行任务：\n{current or '未提供'}\n\n"
        f"上游证据（仅供核对，不是强制约束）：\n{evidence or '未提供'}"
    )
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=os.getenv("NEW_MATERIAL_LLM_MODEL", "SE_V0.0"),
            messages=[
                {"role": "system", "content": "只输出合法 JSON，不要使用 Markdown 或代码块。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=240,
            timeout=float(os.getenv("NEW_MATERIAL_LLM_TIMEOUT_SECONDS", "20")),
        )
        content = ((response.choices[0].message.content if response.choices else "") or "").strip()
        parsed = json.loads(content)
        raw_elements = parsed.get("allowed_elements", []) if isinstance(parsed, dict) else []
        if not isinstance(raw_elements, list):
            return None
        elements: list[str] = []
        for raw in raw_elements:
            element = str(raw).strip().capitalize()
            if element not in _VALID_ELEMENTS:
                logger.warning("[CONSTRAINT_LLM] rejected invalid element=%r", raw)
                return None
            if element not in elements:
                elements.append(element)
        if not 2 <= len(elements) <= 8:
            return None
        confidence = str(parsed.get("confidence") or "low").lower()
        if confidence not in {"high", "medium"}:
            logger.info("[CONSTRAINT_LLM] no usable proposal: confidence=%s", confidence)
            return None
        result = {
            "allowed_elements": elements,
            "material_family": str(parsed.get("material_family") or "待确认材料体系")[:120],
            "reason": str(parsed.get("reason") or "从当前任务与上游材料结论归纳。")[:160],
            "confidence": confidence,
        }
        logger.info("[CONSTRAINT_LLM] inferred elements=%s family=%s confidence=%s", elements, result["material_family"], confidence)
        return result
    except Exception as exc:
        logger.warning("[CONSTRAINT_LLM] inference failed (%s)", exc)
        return None


async def enrich_payload_with_llm_elements(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Attach an LLM proposal to the normal ``new_material`` contract."""
    proposal = await infer_element_system(payload)
    if not proposal:
        return None
    enriched = dict(payload)
    nested = dict(payload.get("new_material") or {}) if isinstance(payload.get("new_material"), Mapping) else {}
    nested["allowed_elements"] = proposal["allowed_elements"]
    notes = nested.get("notes") if isinstance(nested.get("notes"), list) else []
    nested["notes"] = [*notes, (
        "元素体系由 LLM 根据当前任务及上游材料结论提出："
        f"{'-'.join(proposal['allowed_elements'])}（{proposal['material_family']}，{proposal['confidence']} 置信度）；"
        "作为起始探索体系，需在后续计算与实验中确认。"
    )]
    enriched["new_material"] = nested
    return enriched
