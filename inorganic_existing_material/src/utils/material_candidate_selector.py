# -*- coding: utf-8 -*-
import json
import re


async def llm_select_material_candidates(llm, logger, raw_tokens: list, user_context: str = "", in_ls_summary: dict = None) -> tuple:
    raw_list = [str(x).strip() for x in (raw_tokens or []) if str(x).strip()]
    if not raw_list:
        return [], [], [], []

    in_ls_summary = in_ls_summary if isinstance(in_ls_summary, dict) else {}
    prompt = (
        "你是材料候选抽取校正器。任务：根据用户上下文与候选 token，判断哪些应保留为当前页候选展示，哪些可以进入后续 MP 检索。"
        "这里只判断‘像不像应该保留的材料/化学式候选’。"
        "请特别过滤单位、工艺词、测试术语、时间戳、编号等噪声，例如 m·K、GPa、XRD。"
        "如果候选是材料名称/材料体系而非严格化学式，可放入 display_tokens，但不要放入 mp_tokens。"
        "如果候选是缩写或化学式，可同时进入 display_tokens；只有明确适合后续 MP 检索时才进入 mp_tokens。"
        "输出必须是 JSON，且仅输出 JSON，不要附加解释。"
        "JSON 结构固定为："
        "{\"display_tokens\":[],\"mp_tokens\":[],\"dropped_tokens\":[{\"token\":\"...\",\"reason\":\"...\"}],\"non_mp_notes\":[]}"
        f"\n用户原文上下文：{str(user_context or '')}"
        f"\n上游 in-LS 摘要：{json.dumps(in_ls_summary, ensure_ascii=False)}"
        f"\n候选 token 列表：{json.dumps(raw_list, ensure_ascii=False)}"
    )

    try:
        out = await llm.aask(prompt, stream=False, timeout=30)
        txt = str(out or "").strip()
        if not txt:
            raise ValueError("empty_llm_response")

        payload = None
        try:
            payload = json.loads(txt)
        except Exception:
            payload = None

        if payload is None:
            m_code = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", txt, flags=re.IGNORECASE)
            if m_code:
                try:
                    payload = json.loads(m_code.group(1).strip())
                except Exception:
                    payload = None

        if payload is None:
            m = re.search(r"\{[\s\S]*\}", txt)
            if m:
                payload = json.loads(m.group(0))

        if not isinstance(payload, dict):
            raise ValueError(f"non_json_payload:{txt[:200]}")

        display_tokens = [str(x).strip() for x in (payload.get("display_tokens") or []) if str(x).strip()]
        mp_tokens = [str(x).strip() for x in (payload.get("mp_tokens") or []) if str(x).strip()]
        non_mp_notes = [str(x).strip() for x in (payload.get("non_mp_notes") or []) if str(x).strip()]
        dropped_items = []
        for item in (payload.get("dropped_tokens") or []):
            if isinstance(item, dict):
                tk = str(item.get("token") or "").strip()
                rs = str(item.get("reason") or "llm_drop")
                if tk:
                    dropped_items.append((tk, rs))
            elif isinstance(item, str) and item.strip():
                dropped_items.append((item.strip(), "llm_drop"))

        display_tokens = list(dict.fromkeys([x for x in display_tokens if x in raw_list]))
        mp_tokens = list(dict.fromkeys([x for x in mp_tokens if x in display_tokens or x in raw_list]))
        return display_tokens, mp_tokens, non_mp_notes, dropped_items
    except Exception as e:
        try:
            logger.warning(f"[LLM_CANDIDATE_SELECT] raw_response_preview={str(locals().get('txt', ''))[:200]}")
        except Exception:
            pass
        logger.warning(f"[LLM_CANDIDATE_SELECT] failed, fallback to rule-based lists: {e!s}")
        return None, None, None, None


async def build_candidate_lists(
    llm,
    logger,
    raw_tokens: list,
    user_context: str,
    in_ls_summary: dict,
    to_ascii_formula,
    looks_like_formula,
    normalize_formula_for_mp,
    elements_set,
):
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
    EXCLUDE_TECH_TOKENS = {"GC-MS", "GCMS", "XRD", "XPS", "SEM", "TEM", "EDS", "AFM", "FTIR", "RAMAN", "ALD", "CVD", "PVD", "PLD", "SPS"}

    def _norm_tok(t: str) -> str:
        return str(t or "").strip().replace("＋", "+")

    def _is_chem_piece(t: str) -> bool:
        s = to_ascii_formula(str(t or "").strip())
        if not s:
            return False
        if looks_like_formula(s):
            return True
        if s in elements_set:
            return True
        return bool(re.fullmatch(r"[A-Z]{2,8}", s))

    def _is_system_token(t: str) -> bool:
        if not t or looks_like_formula(t) or not any(x in t for x in ["-", "+", "·", "/"]):
            return False
        parts = [p.strip().strip("()").strip("（）").strip() for p in re.split(r"[\-\+·/]", str(t)) if str(p).strip()]
        if len(parts) < 2:
            return False
        chem_hits = sum(1 for p in parts if _is_chem_piece(p))
        return chem_hits >= 2

    def _looks_like_hybrid_formula_notation(t: str) -> bool:
        s = to_ascii_formula(str(t or "").strip())
        if not s:
            return False
        return bool(re.search(r"Pb", s) and re.search(r"(?i)(MA|FA)", s) and re.search(r"[0-9]", s))

    def _hybrid_to_mp_surrogates(t: str) -> list:
        s = to_ascii_formula(str(t or "").strip())
        if not s:
            return []
        out = []
        seen_local = set()
        if re.fullmatch(r"(?i)MAPbI3", s):
            for cand in ("CH6I3NPb", "PbI3"):
                if looks_like_formula(cand) and cand not in seen_local:
                    out.append(cand)
                    seen_local.add(cand)
            return out

        s2 = re.sub(r"\((?:[^\)]*?(?:MA|FA)[^\)]*?)\)\d*(?:\.\d+)?", "", s, flags=re.IGNORECASE)
        s2 = re.sub(r"(?:MA|FA)\d*(?:\.\d+)?", "", s2, flags=re.IGNORECASE)
        s2 = re.sub(r"\s+", "", s2)
        if looks_like_formula(s2):
            nf = normalize_formula_for_mp(s2) or s2
            if nf not in seen_local:
                out.append(nf)
                seen_local.add(nf)

        for m in re.finditer(r"(?:[A-Z][a-z]?\d*(?:\.\d+)?|\([A-Za-z0-9\.]+\)\d*(?:\.\d+)?) {0,}", s.replace(" ", "")):
            seg = m.group(0).strip()
            if not seg or re.search(r"(?i)(MA|FA)", seg):
                continue
            if looks_like_formula(seg):
                nf = normalize_formula_for_mp(seg) or seg
                if nf not in seen_local:
                    out.append(nf)
                    seen_local.add(nf)
        return out

    def _explode_system_to_mp_tokens(t: str) -> list:
        s = to_ascii_formula(str(t or "").strip())
        if not s:
            return []
        parts = [p.strip().strip("()").strip("（）").strip() for p in re.split(r"[\-\+·/]", s) if str(p).strip()]
        out = []
        seen_local = set()
        for p in parts:
            p2 = re.sub(r"^(?:[nNxXyYzZmMkK])+", "", p).strip()
            if not p2:
                continue
            if looks_like_formula(p2):
                nf = normalize_formula_for_mp(p2) or p2
                if nf not in seen_local:
                    out.append(nf)
                    seen_local.add(nf)
        return out

    def _extract_locked_tokens_from_inls_summary(summary: dict) -> list:
        if not isinstance(summary, dict):
            return []
        cands = []
        for k in ("baseline_material", "advanced_material"):
            v = str(summary.get(k) or "").strip()
            if not v:
                continue
            parts = [p.strip() for p in re.split(r"[\s,/+;，、]+", v) if p.strip()]
            for p in parts:
                p2 = to_ascii_formula(p)
                if p2:
                    cands.append(p2)
        return list(dict.fromkeys(cands))

    def _is_noise_token(t: str) -> bool:
        s = str(t or "").strip()
        if not s:
            return True
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[Tt]\d{1,2}(?::\d{1,2}(?::\d{1,2})?)?)?", s):
            return True
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", s):
            return True
        s_up = s.upper()
        if s_up in EXCLUDE_GAS_TOKENS or s_up in EXCLUDE_TECH_TOKENS:
            return True
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
        if "-" in s:
            parts = [p.strip() for p in s.split("-") if p.strip()]
            if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z]{2,}", p) for p in parts):
                if not all(p in elements_set for p in parts):
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

    locked_tokens = _extract_locked_tokens_from_inls_summary(in_ls_summary)
    for lt in locked_tokens:
        if lt not in seen:
            display_tokens.append(lt)
            seen.add(lt)

    mp_tokens = []
    mp_seen = set()
    non_mp_notes = []
    for t in display_tokens:
        key = re.sub(r"\s+", "", str(t).upper())
        if key in ABBR_FORMULA_MAP:
            mapped = ABBR_FORMULA_MAP[key]
            if looks_like_formula(mapped) and mapped not in mp_seen:
                mp_tokens.append(mapped)
                mp_seen.add(mapped)
            elif mapped not in mp_seen:
                dropped_tokens.append((t, f"abbr_mapped_non_mp_formula:{mapped}"))
            non_mp_notes.append(f"`{t}` 识别为材料缩写，仅在映射后参与 MP 检索。")
            continue
        if looks_like_formula(t):
            mp_formula = normalize_formula_for_mp(t) or t
            if mp_formula not in mp_seen:
                mp_tokens.append(mp_formula)
                mp_seen.add(mp_formula)
            if mp_formula != t:
                non_mp_notes.append(f"`{t}` 已归一为 `{mp_formula}` 后参与 MP 检索。")
            continue
        if _is_system_token(t):
            non_mp_notes.append(f"`{t}` 为体系/复合表达，仅用于展示，不直接参与 MP 检索。")
            exploded = _explode_system_to_mp_tokens(t)
            for _mp in exploded:
                if _mp not in mp_seen:
                    mp_tokens.append(_mp)
                    mp_seen.add(_mp)
            if exploded:
                non_mp_notes.append(f"`{t}` 已拆解为 {exploded} 参与 MP 检索。")
            continue
        if _looks_like_hybrid_formula_notation(t):
            non_mp_notes.append(f"`{t}` 识别为混合有机-无机化学式记法，已保留展示并尝试提取无机骨架参与 MP。")
            for _mp in _hybrid_to_mp_surrogates(t):
                if _mp not in mp_seen:
                    mp_tokens.append(_mp)
                    mp_seen.add(_mp)
        else:
            dropped_tokens.append((t, "not_formula_or_system"))

    llm_display, llm_mp, llm_notes, llm_dropped = await llm_select_material_candidates(
        llm=llm,
        logger=logger,
        raw_tokens=display_tokens,
        user_context=user_context,
        in_ls_summary=in_ls_summary,
    )

    if isinstance(llm_display, list):
        if len(llm_display) == 0 and len(display_tokens) > 0:
            dropped_tokens = list(dict.fromkeys(dropped_tokens + (llm_dropped or [])))
            non_mp_notes = list(dict.fromkeys((non_mp_notes or [])))
        else:
            rule_mp_tokens = list(mp_tokens or [])
            rule_non_mp = set(non_mp_notes)
            non_mp_notes = list(dict.fromkeys((llm_notes or []) + [x for x in non_mp_notes if x not in rule_non_mp or x]))
            dropped_tokens = list(dict.fromkeys(dropped_tokens + (llm_dropped or [])))
            display_tokens = llm_display
            llm_mp_tokens = [x for x in (llm_mp or []) if x in display_tokens or looks_like_formula(x)]
            if len(llm_mp_tokens) == 0 and len(rule_mp_tokens) > 0:
                mp_tokens = rule_mp_tokens
                try:
                    logger.info("[ROUTER] LLM mp_tokens empty, fallback to rule-based MP candidates")
                except Exception:
                    pass
            else:
                mp_tokens = llm_mp_tokens

    if locked_tokens:
        for lt in locked_tokens:
            if lt not in display_tokens:
                display_tokens.append(lt)
        try:
            logger.info(f"[ROUTER] locked_tokens_before_mp={locked_tokens}")
        except Exception:
            pass
        for lt in locked_tokens:
            if looks_like_formula(lt):
                nlt = normalize_formula_for_mp(lt) or lt
                if nlt not in mp_tokens:
                    mp_tokens.append(nlt)
            elif _is_system_token(lt):
                for _m in _explode_system_to_mp_tokens(lt):
                    if _m not in mp_tokens:
                        mp_tokens.append(_m)

    if len(mp_tokens or []) == 0 and len(display_tokens or []) > 0:
        rebuilt_mp = []
        rebuilt_seen = set()
        for _d in (display_tokens or []):
            for _m in _explode_system_to_mp_tokens(_d):
                if _m not in rebuilt_seen:
                    rebuilt_mp.append(_m)
                    rebuilt_seen.add(_m)
        if rebuilt_mp:
            mp_tokens = rebuilt_mp
            try:
                logger.info("[ROUTER] LLM/rule mp_tokens empty, rebuilt from system tokens")
                logger.info(f"[ROUTER] rebuilt_mp_tokens_from_system={rebuilt_mp}")
            except Exception:
                pass

    mp_tokens_strict = []
    mp_seen_strict = set()
    for _t in (mp_tokens or []):
        _tt = to_ascii_formula(str(_t or "")).strip()
        if not looks_like_formula(_tt):
            continue
        _nf = normalize_formula_for_mp(_tt) or _tt
        if _nf not in mp_seen_strict:
            mp_tokens_strict.append(_nf)
            mp_seen_strict.add(_nf)
    mp_tokens = mp_tokens_strict

    return display_tokens, mp_tokens, non_mp_notes, dropped_tokens
