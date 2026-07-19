"""Formula routing and candidate extraction helpers for inorganic materials."""

from __future__ import annotations

import json
import os
import re

from alpha.logs import logger


def to_ascii_formula(s: str) -> str:
    """Normalize unicode subscripts and separators used in formula text."""
    if s is None:
        return ""
    s = str(s)

    sub_map = str.maketrans({
        "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
        "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    })
    s = s.translate(sub_map)

    # 保留复合表达连接符，避免把 "C2H4Oₙ·LiTFSI·Al2O3" 这类体系拆碎
    s = s.replace("•", "·")
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    return s.strip()


_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg",
    "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr",
    "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br",
    "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd",
    "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La",
    "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er",
    "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au",
    "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md",
    "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
}

_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")


def looks_like_formula(s: str) -> bool:
    s = to_ascii_formula(s)
    if not s:
        return False

    # 聚合占位/复合体系表达，不视作单一可跑 MP 的化学式
    if ("·" in s) or ("ₙ" in s) or re.search(r"_n\b", s, flags=re.IGNORECASE):
        return False

    # 小数点常来自非整数配比或误切 token，当前 MP 检索入口先保守拒绝
    if "." in s:
        return False

    if len(s) < 2 or len(s) > 40:
        return False
    if re.search(r"[^A-Za-z0-9]", s):
        return False

    i = 0
    tokens = []
    while i < len(s):
        match = _FORMULA_TOKEN.match(s, i)
        if not match:
            return False

        sym = match.group(1)
        num = match.group(2)
        if sym not in _ELEMENTS:
            return False
        if num:
            if num.startswith("0"):
                return False
            try:
                n = int(num)
            except Exception:
                return False
            if n <= 0:
                return False

        tokens.append((sym, num))
        i = match.end()

    return not (len(tokens) < 2 and not any(num for _, num in tokens))


def normalize_user_text(s) -> str:
    def _strip_preface_payload_noise(text: str) -> str:
        t = str(text or "")

        # 服务编排常会传入“当前问题 + 前置结果”的拼接文本。
        # 路由阶段必须优先使用用户本轮问题，避免从历史结果中误提取材料 token。
        current_q = re.search(
            r"===\s*当前问题\s*===\s*(?:用户|User)\s*[:：]\s*(.*?)(?=\n\s*===|\Z)",
            t,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if current_q:
            return current_q.group(1).strip()

        # 去掉“前置结果”里常见的整段 JSON payload（仅影响提取输入）
        t = re.sub(
            r"\{[^{}]{0,20000}\"version\"\s*:\s*\"1\.0\.0\"[^{}]{0,20000}\}",
            " ",
            t,
            flags=re.DOTALL,
        )
        t = re.sub(
            r"\{[^{}]{0,20000}\"type\"\s*:\s*\"progress\"[^{}]{0,20000}\}",
            " ",
            t,
            flags=re.DOTALL,
        )
        t = re.sub(
            r"\{[^{}]{0,20000}\"request_id\"\s*:\s*\"[^\"]+\"[^{}]{0,20000}\}",
            " ",
            t,
            flags=re.DOTALL,
        )

        # 若存在“### 需求”，优先从需求正文开始
        anchor = t.find("### 需求")
        if anchor >= 0:
            t = t[anchor:]

        # 若存在“=== 前置结果 ===”，尽量丢弃其前后噪声头
        pre = t.find("=== 前置结果 ===")
        if pre >= 0:
            t = t[pre + len("=== 前置结果 ==="):]

        return t

    if isinstance(s, dict):
        s = (s.get("idea") or s.get("content") or s.get("text") or s.get("query") or "")

    if isinstance(s, list):
        for item in reversed(s):
            if isinstance(item, dict):
                content = item.get("idea") or item.get("content") or item.get("text") or item.get("query")
                if isinstance(content, str) and content.strip():
                    s = content
                    break
            if hasattr(item, "content"):
                content = getattr(item, "content", None)
                if isinstance(content, str) and content.strip():
                    s = content
                    break
            if isinstance(item, str) and item.strip():
                s = item
                break
        else:
            s = ""

    s = str(s or "").strip()
    s = _strip_preface_payload_noise(s)
    m = re.search(r"\[Human:\s*(.*?)\s*\]$", s)
    if m:
        s = m.group(1).strip()
    return s.strip("[](){} \n\t")

def parse_route(s: str):
    s = (s or "").strip()
    m = re.match(r"^/(mp)\s+(.+)$", s, flags=re.IGNORECASE)
    if not m:
        return None, s
    return m.group(1).lower(), m.group(2).strip()

def build_formula_extraction_text(s: str) -> str:
    """
    仅用于“化学式提取”的输入清洗：
    - 不改动原始日志发送逻辑；
    - 只在 fallback 全文检索前去掉协议噪声（如 <<<CONTENT_*>>>）；
    - 若存在“### 需求”，优先从该段开始做提取，避免前置 progress/json 污染。
    """
    t = str(s or "")

    # 前置结果拼接串：优先截取其后正文，避免把 metadata 当成提取源
    if "=== 前置结果 ===" in t:
        t = t.split("=== 前置结果 ===", 1)[-1]

    # 去掉前置结果中常见 JSON payload（仅影响化学式提取输入）
    t = re.sub(
        r"\{[^{}]{0,20000}\"version\"\s*:\s*\"1\.0\.0\"[^{}]{0,20000}\}",
        " ",
        t,
        flags=re.DOTALL,
    )
    # 宽松兜底：删除包含 progress 元数据的 JSON 串（兼容连在一起的片段）
    t = re.sub(
        r"\{[^{}]{0,30000}\"type\"\s*:\s*\"progress\"[^{}]{0,30000}\}",
        " ",
        t,
        flags=re.DOTALL,
    )
    t = re.sub(
        r"\{[^{}]{0,30000}\"agent\"\s*:\s*\"XIMUAlpha_MNS\"[^{}]{0,30000}\}",
        " ",
        t,
        flags=re.DOTALL,
    )
    # time 字段残片（如 2026-03-24T16:16:24.958040）直接清掉
    t = re.sub(r"\"time\"\s*:\s*\"[^\"]{4,64}\"", " ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\{[^{}]{0,20000}\"type\"\s*:\s*\"progress\"[^{}]{0,20000}\}",
        " ",
        t,
        flags=re.DOTALL,
    )
    t = re.sub(
        r"\{[^{}]{0,20000}\"request_id\"\s*:\s*\"[^\"]+\"[^{}]{0,20000}\}",
        " ",
        t,
        flags=re.DOTALL,
    )

    # 去掉协议标记行（仅影响提取输入，不删除任何真实日志）
    t = re.sub(r"<<<CONTENT_(?:START|END):[^>]*>>>", " ", t)

    # 去掉 MATERIAL_RETRIEVAL 整段协议内容（仅影响提取输入）
    t = re.sub(
        r"<<<CONTENT_START:MATERIAL_RETRIEVAL>>>.*?<<<CONTENT_END:MATERIAL_RETRIEVAL>>>",
        " ",
        t,
        flags=re.DOTALL,
    )

    # 去掉包含 MATERIAL_RETRIEVAL / MaterialsPNG / MaterialsGLB 的 JSON 片段（仅影响提取输入）
    t = re.sub(
        r"\{[^{}]{0,12000}(?:\"id\"\s*:\s*\"MATERIAL_RETRIEVAL\"|\"type\"\s*:\s*\"MaterialsPNG\"|\"type\"\s*:\s*\"MaterialsGLB\")[^{}]{0,12000}\}",
        " ",
        t,
        flags=re.DOTALL,
    )

    # 行级兜底：残留协议/资产行不参与化学式提取
    _kept = []
    for _ln in t.splitlines():
        _low = _ln.lower()
        if (
            "material_retrieval" in _low
            or '"type":"materialspng"' in _low
            or '"type":"materialsglb"' in _low
            or "<<<content_start:" in _low
            or "<<<content_end:" in _low
        ):
            continue
        _kept.append(_ln)
    t = "\n".join(_kept)

    # 若存在“需求”正文，优先只用这部分做化学式提取
    anchor_candidates = []
    for _k in ["### 需求", "用户问题", "需求描述", "需求如下"]:
        _idx = t.find(_k)
        if _idx >= 0:
            anchor_candidates.append(_idx)
    if anchor_candidates:
        t = t[min(anchor_candidates):]

    return t

# =========================
# 4) ✅只从“计算对象”行抽取（避免把别的材料带进来）
# =========================
def extract_formulas_from_targets(text: str) -> list:
    text = to_ascii_formula(text or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    ABBR_HINT_TOKENS = {
        "LLZO", "LATP", "LAGP", "LPSCL", "LIPON", "NCM811", "LNMO", "LCO", "NCA"
    }

    EXCLUDE_GAS_TOKENS = {"O2", "CO2", "N2", "H2", "H2O", "CO"}
    EXCLUDE_TECH_TOKENS = {
        "GC-MS", "GCMS", "XRD", "XPS", "SEM", "TEM", "EDS", "AFM", "FTIR", "Raman",
        "ALD", "CVD", "PVD", "PLD", "SPS",
    }

    def _is_spacegroup_like(t: str) -> bool:
        s = str(t or "").strip()
        # 常见空间群短写，如 Fm-3m / R-3m / Pnma / P63/mmc
        return bool(re.fullmatch(r"[A-Z][a-z]?(?:-[0-9][a-z]?)?(?:/[a-z0-9]+)?", s))

    def _is_single_element_formula(t: str) -> bool:
        s = to_ascii_formula(t)
        if not looks_like_formula(s):
            return False
        toks = re.findall(r"([A-Z][a-z]?)(\d*)", s)
        return len(toks) == 1

    def _is_noise_token(tok: str) -> bool:
        t = str(tok or "").strip()
        if not t:
            return True

        # 日期/时间类噪声：2026-03-24 / 2026-03-24T16 / 16:16(:24)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[Tt]\d{1,2}(?::\d{1,2}(?::\d{1,2})?)?)?", t):
            return True
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", t):
            return True

        t_up = t.upper()

        # 常见环境小分子：默认不作为候选材料
        if t_up in EXCLUDE_GAS_TOKENS:
            return True

        if t_up in EXCLUDE_TECH_TOKENS:
            return True

        # 项目编号/仪器编号样式：EEE-INST-002 / ABC-123-XYZ
        if re.fullmatch(r"[A-Z]{2,}(?:-[A-Z0-9]{2,}){1,}", t):
            return True

        if _is_spacegroup_like(t):
            return True

        # 单元素计量式（如 Li7）在兜底里视作噪声，避免误吃
        if _is_single_element_formula(t):
            return True

        # 典型单位串（如 MPa·m / GPa / eV / S/cm）
        if re.search(r"(?i)\b(?:MPA|GPA|PA|EV|KV|MV|W|KW|MW|J|KJ|MJ|V|A|MA|UA|OHM|S/CM)\b", t):
            if "·" in t or "/" in t or re.search(r"(?i)\b(?:MPA|GPA|EV|S/CM)\b", t):
                return True

        # 随机ID样式：多段连字符且字母数字混杂（如 gvn-A0-w7gtIKrk9qijIV）
        if "-" in t and len(t) >= 10:
            parts = [p for p in t.split("-") if p]
            if len(parts) >= 2:
                def _id_like_part(p: str) -> bool:
                    has_alpha = bool(re.search(r"[A-Za-z]", p))
                    has_digit = bool(re.search(r"\d", p))
                    has_upper = bool(re.search(r"[A-Z]", p))
                    has_lower = bool(re.search(r"[a-z]", p))
                    return has_alpha and has_digit and ((has_upper and has_lower) or len(p) >= 6)

                if any(_id_like_part(p) for p in parts):
                    return True

        # 英文描述短语（Cutting-Edge / Solid-State / Sulfide-Based 等）直接过滤
        if "-" in t:
            parts = [p.strip() for p in t.split("-") if p.strip()]
            if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z]{2,}", p) for p in parts):
                if not all(p in _ELEMENTS for p in parts):
                    return True

        # 工艺词-化学式/术语-化学式，保留后者，不把整体当候选
        if "-" in t and len(t.split("-")) == 2:
            a, b = t.split("-", 1)
            if a.strip().upper() in EXCLUDE_TECH_TOKENS or b.strip().upper() in EXCLUDE_TECH_TOKENS:
                return True

        return False

    targets = []
    for ln in lines:
        m = re.search(r"计算对象\s*\d+\s*\(.*?\)\s*[:：]\s*([A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{2,40})", ln)
        if m:
            tok = to_ascii_formula(m.group(1))
            tok_up = str(tok).upper()
            if (looks_like_formula(tok) or tok_up in ABBR_HINT_TOKENS) and (not _is_noise_token(tok)):
                targets.append(tok)

    seen = set()
    out = []
    for x in targets:
        if x not in seen:
            out.append(x)
            seen.add(x)

    # fallback：全局兜底（保留复合/聚合表达，不拆碎）
    composite_pat = re.compile(
        r"(?:[A-Za-z0-9₀₁₂₃₄₅₆₇₈₉ₙ\(\)]+(?:[·\-][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉ₙ\(\)]+)+)"
    )
    composite_spans = []
    for m in composite_pat.finditer(text):
        tok = m.group(0).strip()
        if tok and any(ch.isupper() for ch in tok) and (not _is_noise_token(tok)):
            if tok not in seen:
                out.append(tok)
                seen.add(tok)
            composite_spans.append((m.start(), m.end()))

    # 兼容类似 C2H4Oₙ 这类单段聚合写法
    polymer_pat = re.compile(r"\b[A-Z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]*ₙ\b")
    for m in polymer_pat.finditer(text):
        tok = m.group(0).strip()
        if tok and (not _is_noise_token(tok)) and tok not in seen:
            out.append(tok)
            seen.add(tok)
        composite_spans.append((m.start(), m.end()))

    tokens = re.finditer(r"\b[A-Z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{1,39}\b", text)
    for m in tokens:
            tok = m.group(0)

            # 如果在复合/聚合表达片段内部，跳过，避免被拆成 C2H4 这类碎片
            if any(m.start() >= a and m.end() <= b for a, b in composite_spans):
                    continue

            # ✅ 最小改动：如果 token 左右紧贴 '.'，说明来自小数配方（如 Ni₀.₈ / XX5.4），直接跳过
            left = text[m.start() - 1] if m.start() - 1 >= 0 else ""
            right = text[m.end()] if m.end() < len(text) else ""
            if left == "." or right == ".":
                    continue

            tok2 = to_ascii_formula(tok)
            tok2_up = str(tok2).upper()
            if (looks_like_formula(tok2) or tok2_up in ABBR_HINT_TOKENS) and (not _is_noise_token(tok2)) and tok2 not in seen:
                    out.append(tok2)
                    seen.add(tok2)
    return out

def extract_formulas_from_in_ls(repo_root: str) -> list:
    """
    第三来源：读取 in-LS 最新 json 中的结构候选。
    当前优先字段：
      - simulation_task.baseline_material
      - simulation_task.advanced_material
    """
    in_ls_dir = os.path.join(
        repo_root,
        "src", "MNS_CaseHub", "cases", "material_discovery_demo", "results", "in-LS"
    )
    if not os.path.isdir(in_ls_dir):
        return []

    try:
        cands = [
            os.path.join(in_ls_dir, fn)
            for fn in os.listdir(in_ls_dir)
            if fn.lower().endswith(".json")
        ]
        if not cands:
            return []
        latest = max(cands, key=lambda p: os.path.getmtime(p))
        with open(latest, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception as e:
        logger.warning(f"[IN_LS] read latest json failed: {e!s}")
        return []

    tokens = []
    try:
        st = obj.get("simulation_task") if isinstance(obj, dict) else None
        if isinstance(st, dict):
            for k in ("baseline_material", "advanced_material"):
                v = st.get(k)
                if isinstance(v, str) and v.strip():
                    tokens.append(to_ascii_formula(v.strip()))
    except Exception as e:
        logger.warning(f"[IN_LS] parse json failed: {e!s}")

    # 去重且保持顺序
    seen = set()
    out = []
    for t in tokens:
        if t and t not in seen:
            out.append(t)
            seen.add(t)

    if out:
        logger.info(f"[IN_LS] loaded tokens from {in_ls_dir}: {out}")
    return out

def build_candidate_lists(raw_tokens: list):
    """
    分层处理候选：
    - display_tokens: 前端展示候选（体系表达/缩写/标准化学式）
    - mp_tokens: 仅可用于 MP 检索的标准化学式
    """
    ABBR_FORMULA_MAP = {
        "LLZO": "Li7La3Zr2O12",
        "LATP": "Li1.3Al0.3Ti1.7(PO4)3",
        "LAGP": "Li1.5Al0.5Ge1.5(PO4)3",
        "LPSCL": "Li6PS5Cl",
        "LIPON": "LiPON",
        "NCM811": "NCM811",
        "LNMO": "LNMO",
        "LCO": "LCO",
        "NCA": "NCA",
    }
    EXCLUDE_GAS_TOKENS = {"O2", "CO2", "N2", "H2", "H2O", "CO"}
    EXCLUDE_TECH_TOKENS = {
        "GC-MS", "GCMS", "XRD", "XPS", "SEM", "TEM", "EDS", "AFM", "FTIR", "RAMAN",
        "ALD", "CVD", "PVD", "PLD", "SPS",
    }

    def _norm_tok(t: str) -> str:
        return str(t or "").strip().replace("＋", "+")

    def _is_chem_piece(t: str) -> bool:
        s = to_ascii_formula(str(t or "").strip())
        if not s:
            return False
        if looks_like_formula(s):
            return True
        if s in _ELEMENTS:
            return True
        if re.fullmatch(r"[A-Z]{2,8}", s):
            return True
        return False

    def _is_system_token(t: str) -> bool:
        if not t:
            return False
        if looks_like_formula(t):
            return False
        if not any(x in t for x in ["-", "+", "·", "/"]):
            return False
        parts = [
            p.strip().strip("()").strip("（）").strip()
            for p in re.split(r"[\-\+·/]", str(t))
            if str(p).strip()
        ]
        if len(parts) < 2:
            return False
        chem_hits = sum(1 for p in parts if _is_chem_piece(p))
        return chem_hits >= 2

    def _is_noise_token(t: str) -> bool:
        s = str(t or "").strip()
        if not s:
            return True

        # 日期/时间类噪声：2026-03-24 / 2026-03-24T16 / 16:16(:24)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[Tt]\d{1,2}(?::\d{1,2}(?::\d{1,2})?)?)?", s):
            return True
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", s):
            return True

        s_up = s.upper()
        if s_up in EXCLUDE_GAS_TOKENS:
            return True

        if s_up in EXCLUDE_TECH_TOKENS:
            return True

        # 项目编号/仪器编号样式：EEE-INST-002 / ABC-123-XYZ
        if re.fullmatch(r"[A-Z]{2,}(?:-[A-Z0-9]{2,}){1,}", s):
            return True

        if re.fullmatch(r"[A-Z][a-z]?(?:-[0-9][a-z]?)?(?:/[a-z0-9]+)?", s):
            return True

        toks = re.findall(r"([A-Z][a-z]?)(\d*)", to_ascii_formula(s))
        if looks_like_formula(s) and len(toks) == 1:
            return True

        if re.search(r"(?i)\b(?:MPA|GPA|PA|EV|KV|MV|W|KW|MW|J|KJ|MJ|V|A|MA|UA|OHM|S/CM)\b", s):
            if "·" in s or "/" in s or re.search(r"(?i)\b(?:MPA|GPA|EV|S/CM)\b", s):
                return True

        if "-" in s and len(s) >= 10:
            parts = [p for p in s.split("-") if p]
            if len(parts) >= 2:
                def _id_like_part(p: str) -> bool:
                    has_alpha = bool(re.search(r"[A-Za-z]", p))
                    has_digit = bool(re.search(r"\d", p))
                    has_upper = bool(re.search(r"[A-Z]", p))
                    has_lower = bool(re.search(r"[a-z]", p))
                    return has_alpha and has_digit and ((has_upper and has_lower) or len(p) >= 6)

                if any(_id_like_part(p) for p in parts):
                    return True

        # 英文描述短语（Cutting-Edge / Solid-State / Sulfide-Based 等）直接过滤
        if "-" in s:
            parts = [p.strip() for p in s.split("-") if p.strip()]
            if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z]{2,}", p) for p in parts):
                if not all(p in _ELEMENTS for p in parts):
                    return True

        if "-" in s and len(s.split("-")) == 2:
            a, b = s.split("-", 1)
            if a.strip().upper() in EXCLUDE_TECH_TOKENS or b.strip().upper() in EXCLUDE_TECH_TOKENS:
                return True

        return False

    display_tokens = []
    dropped_tokens = []
    seen = set()
    for t in (raw_tokens or []):
        nt = _norm_tok(t)
        if not nt:
            continue
        if _is_noise_token(nt):
            dropped_tokens.append((nt, "noise_token"))
            continue
        if nt not in seen:
            display_tokens.append(nt)
            seen.add(nt)

    mp_tokens = []
    mp_seen = set()
    non_mp_notes = []

    for t in display_tokens:
        key = re.sub(r"\s+", "", str(t).upper())

        # 缩写：先映射，映射后才进入 MP
        if key in ABBR_FORMULA_MAP:
            mapped = ABBR_FORMULA_MAP[key]
            if looks_like_formula(mapped) and mapped not in mp_seen:
                mp_tokens.append(mapped)
                mp_seen.add(mapped)
            elif mapped not in mp_seen:
                dropped_tokens.append((t, f"abbr_mapped_non_mp_formula:{mapped}"))
            non_mp_notes.append(f"`{t}` 识别为材料缩写，仅在映射后参与 MP 检索。")
            continue

        # 标准化学式：可直接参与 MP
        if looks_like_formula(t):
            if t not in mp_seen:
                mp_tokens.append(t)
                mp_seen.add(t)
            continue

        # 体系表达：仅展示，不直接跑 MP
        if _is_system_token(t):
            non_mp_notes.append(f"`{t}` 为体系/复合表达，仅用于展示，不直接参与 MP 检索。")
        else:
            dropped_tokens.append((t, "not_formula_or_system"))

    return display_tokens, mp_tokens, non_mp_notes, dropped_tokens
