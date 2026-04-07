#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OpenPoly XGB inference helper.

Usage example:
python tools/openpoly_xgb_infer.py \
  --psmiles "[*]CC([*])" \
  --model-dir /data/se42/alpha_project/organic_existing_material/src/MNS_CaseHub/cases/material_discovery_demo/models/openpoly/xgb
"""

import argparse
import json
import os
import sys
from typing import Dict, Optional

import numpy as np


PROPERTIES = [
    "Tg",
    "Td",
    "Tm",
    "Water_Uptake",
    "Dielectric_Constant_Total",
    "Thermal_Conductivity",
]


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _load_model(path: str):
    import joblib

    return joblib.load(path)


def _infer_n_bits(model_dir: str) -> int:
    for p in PROPERTIES:
        mp = os.path.join(model_dir, f"{p}_xgb_model.joblib")
        if not os.path.exists(mp):
            continue
        try:
            m = _load_model(mp)
            n = int(getattr(m, "n_features_in_", 0) or 0)
            if n > 0:
                return n
        except Exception:
            continue
    return 2048


def _psmiles_to_fp(psmiles: str, n_bits: int, radius: int = 2) -> np.ndarray:
    from rdkit import Chem, DataStructs
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(str(psmiles or ""))
    if mol is None:
        raise ValueError("invalid PSMILES/SMILES for RDKit")

    bitvect = AllChem.GetMorganFingerprintAsBitVect(mol, radius=radius, nBits=int(n_bits))
    arr = np.zeros((int(n_bits),), dtype=np.float32)
    DataStructs.ConvertToNumpyArray(bitvect, arr)
    return arr


def infer(psmiles: str, model_dir: str, radius: int = 2) -> Dict:
    out = {
        "ok": True,
        "psmiles": psmiles,
        "model_dir": model_dir,
        "n_bits": None,
        "predictions": {},
        "errors": {},
    }

    if not os.path.isdir(model_dir):
        out["ok"] = False
        out["errors"]["model_dir"] = f"not found: {model_dir}"
        return out

    try:
        n_bits = _infer_n_bits(model_dir)
        out["n_bits"] = int(n_bits)
        fp = _psmiles_to_fp(psmiles, n_bits=n_bits, radius=radius).reshape(1, -1)
    except Exception as e:
        out["ok"] = False
        out["errors"]["feature"] = str(e)
        for p in PROPERTIES:
            out["predictions"][p] = None
        return out

    for p in PROPERTIES:
        model_path = os.path.join(model_dir, f"{p}_xgb_model.joblib")
        if not os.path.exists(model_path):
            out["predictions"][p] = None
            out["errors"][p] = f"model missing: {model_path}"
            continue
        try:
            model = _load_model(model_path)
            pred = model.predict(fp)
            val = _safe_float(pred[0] if hasattr(pred, "__len__") else pred)
            out["predictions"][p] = val
            if val is None:
                out["errors"][p] = "prediction is not numeric"
        except Exception as e:
            out["predictions"][p] = None
            out["errors"][p] = str(e)

    return out


def main():
    parser = argparse.ArgumentParser(description="OpenPoly XGB inference")
    parser.add_argument("--psmiles", required=True, help="input polymer smiles")
    parser.add_argument("--model-dir", required=True, help="directory containing *_xgb_model.joblib")
    parser.add_argument("--radius", type=int, default=2)
    args = parser.parse_args()

    try:
        result = infer(psmiles=args.psmiles, model_dir=args.model_dir, radius=args.radius)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
