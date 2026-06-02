import re


ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn",
    "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr",
}


def to_ascii_formula(s: str) -> str:
    if s is None:
        return ""
    t = str(s)
    trans = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")
    t = t.translate(trans)
    # 常见中文符号/全角字符
    t = t.replace("（", "(").replace("）", ")")
    t = t.replace("【", "[").replace("】", "]")
    t = t.replace("，", ",").replace("；", ";").replace("：", ":")
    t = t.replace("＋", "+").replace("－", "-")
    return t


def looks_like_formula(s: str) -> bool:
    t = to_ascii_formula(s).strip()
    if not t:
        return False

    # 严格限制可用字符（允许括号、点、中点、短横线）
    if not re.fullmatch(r"[A-Za-z0-9\(\)\[\]\+\-·\.]+", t):
        return False

    # 逐段解析元素+计量
    i = 0
    n = len(t)
    tokens = []
    while i < n:
        ch = t[i]
        if ch in "()[]+-·.":
            i += 1
            continue
        m = re.match(r"([A-Z][a-z]?)(\d*)", t[i:])
        if not m:
            return False
        sym, num = m.group(1), m.group(2)
        if sym not in ELEMENTS:
            return False
        if num:
            if num.startswith("0"):
                return False
            try:
                v = int(num)
            except Exception:
                return False
            if v <= 0:
                return False
        tokens.append((sym, num))
        i += m.end()

    if len(tokens) < 2 and not any(num for _, num in tokens):
        return False
    return True


def normalize_formula_for_mp(s: str) -> str:
    src = to_ascii_formula(s).strip()
    if not src:
        return ""
    try:
        from pymatgen.core import Composition
        return str(Composition(src).reduced_formula or "").strip()
    except Exception:
        return ""


def build_formula_extraction_text(s: str) -> str:
    t = str(s or "")
    if "=== 前置结果 ===" in t:
        t = t.split("=== 前置结果 ===", 1)[-1]

    t = re.sub(r"\{[^{}]{0,20000}\"version\"\s*:\s*\"1\.0\.0\"[^{}]{0,20000}\}", " ", t, flags=re.DOTALL)
    t = re.sub(r"\{[^{}]{0,30000}\"type\"\s*:\s*\"progress\"[^{}]{0,30000}\}", " ", t, flags=re.DOTALL)
    t = re.sub(r"\{[^{}]{0,30000}\"agent\"\s*:\s*\"XIMUAlpha_MNS\"[^{}]{0,30000}\}", " ", t, flags=re.DOTALL)
    t = re.sub(r"\"time\"\s*:\s*\"[^\"]{4,64}\"", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\{[^{}]{0,20000}\"request_id\"\s*:\s*\"[^\"]+\"[^{}]{0,20000}\}", " ", t, flags=re.DOTALL)

    t = re.sub(r"<<<CONTENT_(?:START|END):[^>]*>>>", " ", t)
    t = re.sub(r"<<<CONTENT_START:MATERIAL_RETRIEVAL>>>.*?<<<CONTENT_END:MATERIAL_RETRIEVAL>>>", " ", t, flags=re.DOTALL)
    t = re.sub(
        r"\{[^{}]{0,12000}(?:\"id\"\s*:\s*\"MATERIAL_RETRIEVAL\"|\"type\"\s*:\s*\"MaterialsPNG\"|\"type\"\s*:\s*\"MaterialsGLB\")[^{}]{0,12000}\}",
        " ",
        t,
        flags=re.DOTALL,
    )

    kept = []
    for ln in t.splitlines():
        low = ln.lower()
        if (
            "material_retrieval" in low
            or '"type":"materialspng"' in low
            or '"type":"materialsglb"' in low
            or "<<<content_start:" in low
            or "<<<content_end:" in low
        ):
            continue
        kept.append(ln)
    t = "\n".join(kept)

    anchors = []
    for k in ["### 需求", "用户问题", "需求描述", "需求如下"]:
        idx = t.find(k)
        if idx >= 0:
            anchors.append(idx)
    if anchors:
        t = t[min(anchors):]
    return t
