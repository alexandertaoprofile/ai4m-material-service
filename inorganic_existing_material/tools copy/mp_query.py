#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from typing import List, Dict, Any, Optional, Tuple

from dotenv import load_dotenv
from mp_api.client import MPRester
from pymatgen.core import Composition

DEFAULT_FIELDS = [
    "material_id",
    "formula_pretty",
    "elements",
    "chemsys",
    "nsites",
    "symmetry",
    "density",
    "volume",
    "energy_above_hull",
    "is_stable",
    "energy_per_atom",
    "formation_energy_per_atom",
    "band_gap",
    "database_IDs",
]

LEGACY_MP_ID_RE = re.compile(r"^mp-\d+$")
MP_ID_RE = re.compile(r"^mp-[a-z0-9]+$")  # numeric OR alphanumeric

def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def _load_key() -> str:
    load_dotenv(os.path.join(_repo_root(), ".env"))
    key = os.getenv("MP_API_KEY") or os.getenv("MAPI_KEY") or os.getenv("MP_API_TOKEN")
    if not key:
        raise RuntimeError("MP_API_KEY not found. Put it in repo_root/.env or export MP_API_KEY.")
    return key.strip()

def _to_dict(doc: Any) -> Dict[str, Any]:
    if hasattr(doc, "model_dump"):
        return doc.model_dump()
    if hasattr(doc, "dict"):
        return doc.dict()
    if isinstance(doc, dict):
        return doc
    try:
        return dict(doc)
    except Exception:
        return {"_raw": str(doc)}

def _as_chemsys(elements: List[str]) -> str:
    return "-".join(sorted(set([e.strip() for e in elements if e.strip()])))

def _normalize_elements(elements: List[str]) -> List[str]:
    out = []
    for e in elements:
        e = e.strip()
        if e:
            out.append(e)
    if not out:
        raise ValueError("Empty elements list.")
    return sorted(set(out))

def _reduced_formula(formula: str) -> str:
    try:
        comp = Composition(formula)
        return comp.reduced_formula
    except Exception as e:
        raise ValueError(f"Invalid formula: {formula} ({e})") from e

def _pick_legacy_numeric_id(d: Dict[str, Any]) -> Optional[str]:
    x = d.get("database_IDs")
    candidates: List[str] = []

    if isinstance(x, str):
        candidates = [x]
    elif isinstance(x, list):
        candidates = [str(i) for i in x]
    elif isinstance(x, dict):
        for v in x.values():
            if v is None:
                continue
            if isinstance(v, list):
                candidates.extend([str(i) for i in v])
            else:
                candidates.append(str(v))

    for c in candidates:
        c = c.strip()
        if LEGACY_MP_ID_RE.match(c):
            return c
    return None

def _extract_all_mp_ids(d: Dict[str, Any]) -> List[str]:
    """
    Return all IDs we can use to match:
      - material_id (mp-xxxxx)
      - legacy numeric id (mp-123)
      - any ids inside database_IDs that look like mp-...
    """
    ids = []
    mid = d.get("material_id")
    if isinstance(mid, str) and MP_ID_RE.match(mid.strip()):
        ids.append(mid.strip())

    legacy = _pick_legacy_numeric_id(d)
    if legacy:
        ids.append(legacy)

    x = d.get("database_IDs")
    candidates: List[str] = []
    if isinstance(x, str):
        candidates = [x]
    elif isinstance(x, list):
        candidates = [str(i) for i in x]
    elif isinstance(x, dict):
        for v in x.values():
            if v is None:
                continue
            if isinstance(v, list):
                candidates.extend([str(i) for i in v])
            else:
                candidates.append(str(v))

    for c in candidates:
        c = str(c).strip()
        if MP_ID_RE.match(c):
            ids.append(c)

    # uniq preserve order
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out

def _pick_summary_item(doc: Any) -> Dict[str, Any]:
    d = _to_dict(doc)
    legacy_id = _pick_legacy_numeric_id(d)
    preferred_id = legacy_id or d.get("material_id")

    sym = d.get("symmetry") or {}
    sym_out = sym
    if isinstance(sym, dict):
        sym_out = {
            "crystal_system": sym.get("crystal_system"),
            "symbol": sym.get("symbol"),
            "number": sym.get("number"),
        }

    item = {
        "preferred_id": preferred_id,
        "material_id": d.get("material_id"),
        "legacy_numeric_id": legacy_id,
        "all_mp_ids": _extract_all_mp_ids(d),
        "formula_pretty": d.get("formula_pretty"),
        "elements": d.get("elements"),
        "chemsys": d.get("chemsys"),
        "nsites": d.get("nsites"),
        "density": d.get("density"),
        "volume": d.get("volume"),
        "energy_above_hull": d.get("energy_above_hull"),
        "is_stable": d.get("is_stable"),
        "energy_per_atom": d.get("energy_per_atom"),
        "formation_energy_per_atom": d.get("formation_energy_per_atom"),
        "band_gap": d.get("band_gap"),
        "symmetry": sym_out,
        "database_IDs": d.get("database_IDs"),
    }
    return item

def _rank_key(it: Dict[str, Any]) -> Tuple[float, float, float]:
    # stable first, then low Ehull, then low formation energy
    stable = it.get("is_stable")
    stable_score = 0.0 if stable is True else 1.0
    eh = it.get("energy_above_hull")
    fe = it.get("formation_energy_per_atom")
    eh = 1e9 if eh is None else float(eh)
    fe = 1e9 if fe is None else float(fe)
    # formation energy negative is "better" so keep as is
    return (stable_score, eh, fe)

def mp_query_by_id(material_id: str, fields: List[str]) -> Dict[str, Any]:
    api_key = _load_key()
    with MPRester(api_key) as mpr:
        docs = mpr.materials.summary.search(
            material_ids=[material_id],
            fields=fields,
            num_chunks=1,
            chunk_size=1,
        )
    docs = list(docs)
    if not docs:
        return {"ok": False, "error": f"material_id not found: {material_id}"}
    item = _pick_summary_item(docs[0])
    return {
        "ok": True,
        "query_mode": "material_id",
        "input": {"material_id": material_id},
        "count": 1,
        "items": [item],
    }

def mp_query_candidates(
    formula: Optional[str],
    elements: Optional[List[str]],
    fetch_n: int,
    fields: List[str],
) -> Dict[str, Any]:
    api_key = _load_key()

    query_mode = None
    target_reduced = None

    if formula:
        query_mode = "formula"
        target_reduced = _reduced_formula(formula)
        comp = Composition(formula)
        elements_list = sorted([str(el) for el in comp.elements])
        chemsys = _as_chemsys(elements_list)
    else:
        query_mode = "elements"
        elements_list = _normalize_elements(elements or [])
        chemsys = _as_chemsys(elements_list)

    with MPRester(api_key) as mpr:
        docs = mpr.materials.summary.search(
            chemsys=chemsys,
            fields=fields,
            num_chunks=1,
            chunk_size=int(fetch_n),
        )
    docs = list(docs)

    # formula exact reduced match
    if query_mode == "formula" and target_reduced:
        filtered = []
        for d in docs:
            try:
                dd = _to_dict(d)
                pretty = dd.get("formula_pretty")
                if pretty and _reduced_formula(pretty) == target_reduced:
                    filtered.append(d)
            except Exception:
                continue
        docs = filtered

    items = [_pick_summary_item(d) for d in docs]
    items = sorted(items, key=_rank_key)

    return {
        "ok": True,
        "query_mode": query_mode,
        "input": {
            "formula": formula,
            "elements": elements,
            "chemsys": chemsys,
            "reduced_formula": target_reduced,
        },
        "count": len(items),
        "items": items,
    }

def shortlist_with_whitelist(
    items: List[Dict[str, Any]],
    whitelist_ids: List[str],
    max_keep: int,
) -> Dict[str, Any]:
    """
    Ensure whitelist ids appear in output (if exist in candidates).
    Fill remaining slots by ranking order (items already sorted).
    """
    wl = [x.strip() for x in (whitelist_ids or []) if x and x.strip()]
    wl_set = set(wl)

    def item_matches_whitelist(it: Dict[str, Any]) -> bool:
        ids = set(it.get("all_mp_ids") or [])
        return len(ids & wl_set) > 0

    forced = [it for it in items if item_matches_whitelist(it)]
    forced_ids = set()
    for it in forced:
        for x in (it.get("all_mp_ids") or []):
            forced_ids.add(x)

    out = []
    seen_material = set()
    def add(it: Dict[str, Any], reason: str):
        mid = it.get("material_id")
        if not mid or mid in seen_material:
            return
        it2 = dict(it)
        it2["selected_reason"] = reason
        out.append(it2)
        seen_material.add(mid)

    # 1) add forced first (keep their original rank order)
    for it in forced:
        add(it, "whitelist")

    # 2) fill to max_keep
    for it in items:
        if len(out) >= max_keep:
            break
        if it.get("material_id") in seen_material:
            continue
        add(it, "top_ranked")

    return {
        "max_keep": max_keep,
        "whitelist_ids": wl,
        "selected_count": len(out),
        "selected": out,
    }

def main():
    parser = argparse.ArgumentParser(description="Query Materials Project summaries.")
    parser.add_argument("--material-id", type=str, default=None, help="e.g. mp-985592 or mp-cebzb")
    parser.add_argument("--formula", type=str, default=None, help="e.g. Li6PS5Cl / Li3PS4")
    parser.add_argument("--elements", nargs="*", default=None, help="e.g. Li P S Cl")
    parser.add_argument("--fetch-n", type=int, default=200, help="How many MP docs to fetch for chemsys then filter.")
    parser.add_argument("--max-keep", type=int, default=5, help="How many candidates to keep for frontend display.")
    parser.add_argument("--whitelist", nargs="*", default=None, help="Force keep these ids if present in candidates.")
    parser.add_argument("--fields", nargs="*", default=DEFAULT_FIELDS)

    args = parser.parse_args()

    try:
        if args.material_id:
            out = mp_query_by_id(args.material_id, fields=args.fields)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            sys.exit(0 if out.get("ok") else 1)

        if not args.formula and not args.elements:
            print(json.dumps({"ok": False, "error": "Need --material-id or (--formula/--elements)"}, ensure_ascii=False))
            sys.exit(2)

        raw = mp_query_candidates(
            formula=args.formula,
            elements=args.elements,
            fetch_n=args.fetch_n,
            fields=args.fields,
        )

        items = raw.get("items") or []
        short = shortlist_with_whitelist(
            items=items,
            whitelist_ids=args.whitelist or [],
            max_keep=int(args.max_keep),
        )

        out = {
            "ok": True,
            "raw_count": raw.get("count"),
            "query_mode": raw.get("query_mode"),
            "input": raw.get("input"),
            "max_keep": short["max_keep"],
            "whitelist": short["whitelist_ids"],
            "count": short["selected_count"],
            "items": short["selected"],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)

if __name__ == "__main__":
    main()
