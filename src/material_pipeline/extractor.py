from __future__ import annotations

import json
import os
import re
from typing import List, Optional

from alpha.logs import logger

from src.llm_utils import SeLLM

from .models import FormulaExtractionResult


_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn",
    "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr",
    "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
}
_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")
_FORMULA_BLOCKLIST = {
    "PINN", "USB", "PCB", "PSO", "IC", "CPU", "GPU", "SOC", "AI", "ML", "LLM"
}

_SEMANTIC_EXPANSION_KEYWORDS = {
    "pcb": [
        "pcb", "封装", "基板", "树脂", "介电", "绝缘", "导热", "热膨胀", "cte", "提纯", "催化剂", "环保",
        "材料", "性能", "初筛", "计算",
    ],
}


def to_ascii_formula(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    sub_map = str.maketrans({
        "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
        "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    })
    s = s.translate(sub_map)
    s = s.replace("·", "").replace("•", "")
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    return s.strip()


def looks_like_formula(s: str) -> bool:
    s = to_ascii_formula(s)
    if not s or "." in s:
        return False
    if s.upper() in _FORMULA_BLOCKLIST:
        return False
    if len(s) < 2 or len(s) > 40:
        return False
    if re.search(r"[^A-Za-z0-9]", s):
        return False

    i = 0
    tokens = []
    while i < len(s):
        m = _FORMULA_TOKEN.match(s, i)
        if not m:
            return False
        sym, num = m.group(1), m.group(2)
        if sym not in _ELEMENTS:
            return False
        if num:
            if num.startswith("0"):
                return False
            try:
                if int(num) <= 0:
                    return False
            except Exception:
                return False
        tokens.append((sym, num))
        i = m.end()

    if len(tokens) < 2 and not any(num for _, num in tokens):
        return False
    return True


class FormulaExtractor:
    def __init__(self, llm: Optional[SeLLM] = None, kb_path: Optional[str] = None):
        self.llm = llm
        self.kb_path = kb_path or os.path.join(os.path.dirname(__file__), "pcb_material_kb.json")
        # self.kb_path =  "/home/ubuntu/Zhuolun_project/MNS_Tuutorial/ALPHA-MNS-main/material-screen-calc/ai4m_tqm/src/material_pipeline/pcb_material_kb.json"

        self.local_kb = self._load_local_kb()

    def _contains_any(self, text: str, words: List[str]) -> bool:
        t = str(text or "").lower()
        return any(str(w).lower() in t for w in words if str(w).strip())

    def _load_local_kb(self) -> List[dict]:
        try:
            with open(self.kb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception as e:
            logger.warning(f"[FormulaExtractor] load local kb failed: {e}")
        return []

    def _search_local_kb(self, prompt: str, max_items: int = 8) -> tuple[List[str], List[str]]:
        text = str(prompt or "").strip().lower()
        if not text or not self.local_kb:
            return [], []

        ranked = []
        for item in self.local_kb:
            title = str(item.get("title") or "")
            aliases = [str(x) for x in (item.get("aliases") or []) if str(x).strip()]
            keywords = [str(x) for x in (item.get("keywords") or []) if str(x).strip()]
            formulas = [to_ascii_formula(str(x)) for x in (item.get("formulas") or []) if str(x).strip()]

            score = 0
            for a in aliases:
                a_l = a.lower()
                if a_l and a_l in text:
                    score += 4
            for k in keywords:
                k_l = k.lower()
                if k_l and k_l in text:
                    score += 2

            # 语义扩展：当需求文本包含“封装树脂/介电/CTE/提纯/催化剂”等泛词时，
            # 允许对 PCB 类知识条目给轻量加权，避免因为用户没写精确词而全空。
            if self._contains_any(text, _SEMANTIC_EXPANSION_KEYWORDS.get("pcb", [])):
                for k in keywords:
                    k_l = k.lower()
                    if k_l in {"pcb", "高性能", "环保", "散热", "陶瓷基板", "基材", "导电"}:
                        score += 1

            if title and title.lower() in text:
                score += 3
            for f in formulas:
                if f and f.lower() in text:
                    score += 5

            if score > 0:
                ranked.append((score, item))

        ranked.sort(key=lambda x: x[0], reverse=True)
        ranked = ranked[:max_items]

        snippets: List[str] = []
        formula_candidates: List[str] = []
        seen = set()
        for _, item in ranked:
            title = str(item.get("title") or "")
            aliases = [str(x) for x in (item.get("aliases") or []) if str(x).strip()]
            formulas = [to_ascii_formula(str(x)) for x in (item.get("formulas") or []) if str(x).strip()]
            snippets.append(f"{title} | aliases={aliases[:4]} | formulas={formulas}")

            for f in formulas:
                if looks_like_formula(f) and f not in seen:
                    seen.add(f)
                    formula_candidates.append(f)

        return snippets, formula_candidates

    def _build_requirement_default_pool(self, prompt: str, max_items: int = 6) -> List[str]:
        """
        requirement_mode_only 下无命中时的稳定兜底：
        - 仅在“材料/树脂/封装/PCB”相关语义下触发
        - 候选来自离线KB，不引入外部硬编码公式
        """
        text = str(prompt or "").lower()
        if not self._contains_any(text, _SEMANTIC_EXPANSION_KEYWORDS.get("pcb", [])):
            return []

        ordered_pool: List[str] = []
        seen = set()

        # 优先顺序：高性能散热/树脂/基材/环保
        priority_keys = ["高性能", "散热", "树脂", "基材", "导电", "环保", "可循环", "生物基"]

        for pk in priority_keys:
            for item in self.local_kb:
                keywords = [str(x).lower() for x in (item.get("keywords") or []) if str(x).strip()]
                if pk.lower() not in keywords:
                    continue
                for f in (item.get("formulas") or []):
                    ff = to_ascii_formula(str(f))
                    if looks_like_formula(ff) and ff not in seen:
                        seen.add(ff)
                        ordered_pool.append(ff)
                        if len(ordered_pool) >= max_items:
                            return ordered_pool

        # 若优先顺序仍不够，补齐整个 KB 的可用式子
        for item in self.local_kb:
            for f in (item.get("formulas") or []):
                ff = to_ascii_formula(str(f))
                if looks_like_formula(ff) and ff not in seen:
                    seen.add(ff)
                    ordered_pool.append(ff)
                    if len(ordered_pool) >= max_items:
                        return ordered_pool

        return ordered_pool

    def _normalize_requirement_prompt(self, prompt: str) -> str:
        """
        兼容上游透传大段“前置结果+当前任务”文本：
        - 优先仅保留“=== 当前任务 ===”之后的内容
        - 清理 CONTENT 标记和行分隔符噪声
        """
        text = str(prompt or "").strip()

        marker = "=== 当前任务 ==="
        if marker in text:
            text = text.split(marker)[-1].strip()

        text = re.sub(r"<<<CONTENT_START:[^>]+>>>", " ", text)
        text = re.sub(r"<<<CONTENT_END:[^>]+>>>", " ", text)
        text = text.replace("%Line Break%", " ")
        return text.strip()

    async def extract(self, prompt: str, requirement_mode_only: bool = False) -> FormulaExtractionResult:
        raw_candidates: List[str] = []
        reject_reasons: List[str] = []
        local_snippets: List[str] = []
        llm_suggested_formulas: List[str] = []
        kb_formulas: List[str] = []
        llm_candidates: List[str] = []
        regex_candidates: List[str] = []
        fallback_pool: List[str] = []

        norm_prompt = self._normalize_requirement_prompt(prompt)

        # 0) 先从本地离线知识库检索，给后续 LLM 提供候选材料上下文
        local_snippets, kb_formulas = self._search_local_kb(norm_prompt)
        raw_candidates.extend(kb_formulas)

        # 1) 用需求 + 本地知识片段，让 LLM 给一批候选化学式
        llm_suggested_formulas = await self._suggest_formulas_from_requirement(norm_prompt, local_snippets)
        raw_candidates.extend(llm_suggested_formulas)

        if not requirement_mode_only:
            # 2) 保留原有直接抽取（用户偶尔会直接给化学式）
            llm_candidates = await self._extract_with_llm(norm_prompt)
            raw_candidates.extend(llm_candidates)

            # 3) 再做 regex 兜底
            regex_candidates = self._extract_with_regex(norm_prompt)
            raw_candidates.extend(regex_candidates)

        # requirement-only 下若全空，启用可控兜底池（来自离线KB）
        if requirement_mode_only and not raw_candidates:
            fallback_pool = self._build_requirement_default_pool(norm_prompt)
            raw_candidates.extend(fallback_pool)

        final_formulas = []
        seen = set()
        for cand in raw_candidates:
            cand = to_ascii_formula(cand)
            if not cand:
                continue
            if not looks_like_formula(cand):
                reject_reasons.append(f"rejected: {cand}")
                continue
            if cand not in seen:
                seen.add(cand)
                final_formulas.append(cand)

        return FormulaExtractionResult(
            final_formulas=final_formulas,
            raw_candidates=raw_candidates,
            web_snippets=local_snippets,
            llm_suggested_formulas=llm_suggested_formulas,
            reject_reasons=reject_reasons,
            source_trace={
                "requirement_mode_only": requirement_mode_only,
                "norm_prompt": norm_prompt,
                "kb_formulas": kb_formulas,
                "llm_suggested_formulas": llm_suggested_formulas,
                "llm_extract_formulas": llm_candidates,
                "regex_formulas": regex_candidates,
                "fallback_pool_formulas": fallback_pool,
            },
        )

    async def _suggest_formulas_from_requirement(self, prompt: str, local_snippets: List[str]) -> List[str]:
        if self.llm is None:
            return []

        snippets_text = "\n".join([f"- {x}" for x in local_snippets[:8]])
        instruction = (
            "你是材料研发助手。根据用户需求与离线材料知识条目，给出候选材料化学式。"
            "只输出 JSON 数组字符串，例如 [\"LiFePO4\",\"LiNi0.8Mn0.1Co0.1O2\"]。"
            "要求：优先给实际常见材料体系，最多8个；如果不确定返回[]；不要输出解释。"
        )

        try:
            messages = [
                self.llm._default_system_msg(),
                self.llm._user_msg(
                    f"{instruction}\n\n用户需求:\n{prompt}\n\n离线知识片段:\n{snippets_text or '[]'}"
                ),
            ]
            response = await self.llm.acompletion_text(
                messages,
                model="SE_V0.0",
                temperature=0.2,
                stream=False,
                timeout=20,
            )
            text = str(response).strip()
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception as e:
            logger.warning(f"[FormulaExtractor] requirement->formula suggest failed: {e}")
        return []

    async def _extract_with_llm(self, prompt: str) -> List[str]:
        if self.llm is None:
            return []

        try:
            instruction = (
                "请从用户文本中提取化学式，输出严格 JSON 数组字符串，不要输出任何解释。"
                "例如：[\"Li6PS5Cl\",\"Li3PS4\"]。"
                "若没有则返回[]。"
            )
            messages = [
                self.llm._default_system_msg(),
                self.llm._user_msg(f"{instruction}\n\n用户文本:\n{prompt}"),
            ]
            response = await self.llm.acompletion_text(
                messages,
                model="SE_V0.0",
                temperature=0.0,
                stream=False,
                timeout=15,
            )
            text = str(response).strip()
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception as e:
            logger.warning(f"[FormulaExtractor] LLM extract failed, fallback regex only: {e}")
        return []

    def _extract_with_regex(self, prompt: str) -> List[str]:
        text = to_ascii_formula(prompt or "")
        out = []
        seen = set()
        for m in re.finditer(r"\b[A-Z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{1,39}\b", text):
            tok = m.group(0)
            left = text[m.start() - 1] if m.start() - 1 >= 0 else ""
            right = text[m.end()] if m.end() < len(text) else ""
            if left == "." or right == ".":
                continue
            tok = to_ascii_formula(tok)
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
        return out
