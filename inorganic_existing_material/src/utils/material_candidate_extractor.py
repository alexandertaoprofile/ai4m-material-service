# -*- coding: utf-8 -*-
import os
import re
import json


def extract_formulas_from_targets(text: str, to_ascii_formula, looks_like_formula, elements_set) -> list:
    text = to_ascii_formula(text or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    ABBR_HINT_TOKENS = {"LLZO", "LATP", "LAGP", "LPSCL", "LIPON", "NCM811", "LNMO", "LCO", "NCA"}
    EXCLUDE_GAS_TOKENS = {"O2", "CO2", "N2", "H2", "H2O", "CO"}
    EXCLUDE_TECH_TOKENS = {"GC-MS", "GCMS", "XRD", "XPS", "SEM", "TEM", "EDS", "AFM", "FTIR", "Raman", "ALD", "CVD", "PVD", "PLD", "SPS"}

    def _is_spacegroup_like(t: str) -> bool:
        return bool(re.fullmatch(r"[A-Z][a-z]?(?:-[0-9][a-z]?)?(?:/[a-z0-9]+)?", str(t or "").strip()))

    def _is_single_element_formula(t: str) -> bool:
        s = to_ascii_formula(t)
        if not looks_like_formula(s):
            return False
        return len(re.findall(r"([A-Z][a-z]?)(\d*)", s)) == 1

    def _is_noise_token(tok: str) -> bool:
        t = str(tok or "").strip()
        if not t:
            return True
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[Tt]\d{1,2}(?::\d{1,2}(?::\d{1,2})?)?)?", t):
            return True
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", t):
            return True
        t_up = t.upper()
        if t_up in EXCLUDE_GAS_TOKENS or t_up in EXCLUDE_TECH_TOKENS:
            return True
        if re.fullmatch(r"[A-Z]{2,}(?:-[A-Z0-9]{2,}){1,}", t):
            return True
        if _is_spacegroup_like(t) or _is_single_element_formula(t):
            return True
        if re.search(r"(?i)\b(?:MPA|GPA|PA|EV|KV|MV|W|KW|MW|J|KJ|MJ|V|A|MA|UA|OHM|S/CM)\b", t) and ("·" in t or "/" in t or re.search(r"(?i)\b(?:MPA|GPA|EV|S/CM)\b", t)):
            return True
        if "-" in t and len(t) >= 10:
            parts = [p for p in t.split("-") if p]
            if len(parts) >= 2:
                def _id_like_part(p: str) -> bool:
                    return bool(re.search(r"[A-Za-z]", p)) and bool(re.search(r"\d", p)) and ((bool(re.search(r"[A-Z]", p)) and bool(re.search(r"[a-z]", p))) or len(p) >= 6)
                if any(_id_like_part(p) for p in parts):
                    return True
        if "-" in t:
            parts = [p.strip() for p in t.split("-") if p.strip()]
            if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z]{2,}", p) for p in parts):
                if not all(p in elements_set for p in parts):
                    return True
        if "-" in t and len(t.split("-")) == 2:
            a, b = t.split("-", 1)
            if a.strip().upper() in EXCLUDE_TECH_TOKENS or b.strip().upper() in EXCLUDE_TECH_TOKENS:
                return True
        return False

    targets, seen, out = [], set(), []
    for ln in lines:
        m = re.search(r"计算对象\s*\d+\s*\(.*?\)\s*[:：]\s*([A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{2,40})", ln)
        if m:
            tok = to_ascii_formula(m.group(1))
            if (looks_like_formula(tok) or str(tok).upper() in ABBR_HINT_TOKENS) and (not _is_noise_token(tok)):
                targets.append(tok)
    for x in targets:
        if x not in seen:
            out.append(x); seen.add(x)

    composite_pat = re.compile(r"(?:[A-Za-z0-9₀₁₂₃₄₅₆₇₈₉ₙ\(\)]+(?:[·\-][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉ₙ\(\)]+)+)")
    composite_spans = []
    for m in composite_pat.finditer(text):
        tok = m.group(0).strip()
        if tok and any(ch.isupper() for ch in tok) and (not _is_noise_token(tok)):
            if tok not in seen:
                out.append(tok); seen.add(tok)
            composite_spans.append((m.start(), m.end()))

    for m in re.finditer(r"\b[A-Z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]*ₙ\b", text):
        tok = m.group(0).strip()
        if tok and (not _is_noise_token(tok)) and tok not in seen:
            out.append(tok); seen.add(tok)
        composite_spans.append((m.start(), m.end()))

    for m in re.finditer(r"\b[A-Z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{1,39}\b", text):
        if any(m.start() >= a and m.end() <= b for a, b in composite_spans):
            continue
        left = text[m.start() - 1] if m.start() - 1 >= 0 else ""
        right = text[m.end()] if m.end() < len(text) else ""
        if left == "." or right == ".":
            continue
        tok2 = to_ascii_formula(m.group(0))
        if (looks_like_formula(tok2) or str(tok2).upper() in ABBR_HINT_TOKENS) and (not _is_noise_token(tok2)) and tok2 not in seen:
            out.append(tok2); seen.add(tok2)
    return out


def extract_formulas_from_in_ls(repo_root: str, to_ascii_formula, looks_like_formula, normalize_formula_for_mp, logger) -> tuple:
    in_ls_dir = os.path.join(repo_root, "src", "MNS_CaseHub", "cases", "material_discovery_demo", "results", "in-LS")
    if not os.path.isdir(in_ls_dir):
        return [], {}

    def _extract_formula_candidates_from_material_label(label: str) -> list:
        s = to_ascii_formula(str(label or "")).strip()
        if not s:
            return []
        out, seen_local = [], set()
        def _try_add(tok: str):
            t = to_ascii_formula(tok).strip().strip("()（）[]{}")
            if not t:
                return
            if looks_like_formula(t):
                t2 = normalize_formula_for_mp(t) or t
                if t2 and t2 not in seen_local:
                    out.append(t2); seen_local.add(t2)
                return
            if re.search(r"(?i)(?:\bMA\b|\bFA\b|MA\d|FA\d)", t) and re.search(r"Pb", t):
                if t not in seen_local:
                    out.append(t); seen_local.add(t)
        for m in re.finditer(r"[\(（]([^\)）]{1,200})[\)）]", s):
            for part in re.split(r"[\s,/+;，、]+", m.group(1)):
                _try_add(part)
        for part in re.split(r"[\s,/+;，、\-·]+", s):
            _try_add(part)
        for m in re.finditer(r"(?:[A-Z][a-z]?\d*(?:\.\d*)?){2,}", s):
            _try_add(m.group(0))
        return out

    try:
        cands = [os.path.join(in_ls_dir, fn) for fn in os.listdir(in_ls_dir) if fn.lower().endswith(".json")]
        if not cands:
            return [], {}
        latest = max(cands, key=lambda p: os.path.getmtime(p))
        with open(latest, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception as e:
        logger.warning(f"[IN_LS] read latest json failed: {e!s}")
        return [], {}

    tokens, summary = [], {}
    try:
        root_obj = obj if isinstance(obj, dict) else {}
        st = root_obj.get("simulation_task") if isinstance(root_obj.get("simulation_task"), dict) else {}
        for src in (root_obj, st):
            if isinstance(src, dict):
                for k in ("baseline_material", "advanced_material", "baseline_reason", "advanced_reason"):
                    v = src.get(k)
                    if isinstance(v, str) and v.strip():
                        tokens.extend(_extract_formula_candidates_from_material_label(v))
        summary = {
            "baseline_material": str(root_obj.get("baseline_material") or st.get("baseline_material") or "").strip(),
            "advanced_material": str(root_obj.get("advanced_material") or st.get("advanced_material") or "").strip(),
            "baseline_reason": str(root_obj.get("baseline_reason") or st.get("baseline_reason") or "").strip(),
            "advanced_reason": str(root_obj.get("advanced_reason") or st.get("advanced_reason") or "").strip(),
        }
    except Exception as e:
        logger.warning(f"[IN_LS] parse json failed: {e!s}")
    seen, out = set(), []
    for t in tokens:
        if t and t not in seen:
            out.append(t); seen.add(t)
    if out:
        logger.info(f"[IN_LS] loaded tokens from {in_ls_dir}: {out}")
    return out, summary
