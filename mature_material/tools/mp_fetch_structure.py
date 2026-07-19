#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from mp_api.client import MPRester

def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def _load_key() -> str:
    load_dotenv(os.path.join(_repo_root(), ".env"))
    key = os.getenv("MP_API_KEY") or os.getenv("MAPI_KEY") or os.getenv("MP_API_TOKEN")
    if not key:
        raise RuntimeError("MP_API_KEY not found. Put it in repo_root/.env or export MP_API_KEY.")
    return key.strip()

def _to_dict(x: Any) -> Dict:
    if x is None:
        return {}
    if hasattr(x, "model_dump"):
        return x.model_dump()
    if hasattr(x, "dict"):
        return x.dict()
    if isinstance(x, dict):
        return x
    return {"_raw": str(x)}

def _extract_structure(doc: Any) -> Optional[Any]:
    if doc is None:
        return None
    if hasattr(doc, "structure"):
        s = getattr(doc, "structure", None)
        if s is not None:
            return s
    d = _to_dict(doc)
    if "structure" in d:
        return d.get("structure")
    return None

def main():
    ap = argparse.ArgumentParser(description="Fetch MP structure and write CIF/JSON.")
    ap.add_argument("--material-id", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--fmt", default="both", choices=["cif", "json", "both"])
    args = ap.parse_args()

    try:
        api_key = _load_key()
        with MPRester(api_key) as mpr:
            docs = mpr.materials.summary.search(
                material_ids=[args.material_id],
                fields=["material_id", "structure"],
                num_chunks=1,
                chunk_size=1,
            )
        docs = list(docs)
        if not docs:
            raise RuntimeError(f"material_id not found: {args.material_id}")

        s = _extract_structure(docs[0])
        if s is None:
            raise RuntimeError(f"Cannot extract structure for {args.material_id}")

        os.makedirs(args.out_dir, exist_ok=True)
        written = {}

        if args.fmt in ("cif", "both"):
            cif_path = os.path.join(args.out_dir, "structure.cif")
            with open(cif_path, "w", encoding="utf-8") as f:
                f.write(s.to(fmt="cif"))
            written["cif"] = cif_path

        if args.fmt in ("json", "both"):
            json_path = os.path.join(args.out_dir, "structure.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(s.as_dict(), f, ensure_ascii=False, indent=2)
            written["json"] = json_path

        print(json.dumps({"ok": True, "material_id": args.material_id, "out_dir": args.out_dir, "written": written},
                         ensure_ascii=False, indent=2))
        sys.exit(0)

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)

if __name__ == "__main__":
    main()
