"""JSON-in/JSON-out runner; execute only in the isolated model environment."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

# When invoked as ``python /.../tools/service_runner.py``, Python only adds
# tools/ to sys.path.  Add the repository root so ``src`` imports work from a
# micromamba subprocess as well as from direct development execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.runtime import dispatch as hea_dispatch
from src.api.ni_superalloy_creep_runtime import dispatch as ni_creep_dispatch
from src.api.ni_superalloy_hotend_runtime import dispatch as ni_hotend_dispatch
from src.api.reusable_rocket_stainless_runtime import dispatch as rocket_stainless_dispatch
from src.api.chip_glass_cte_runtime import dispatch as chip_glass_cte_dispatch
from src.api.chip_glass_thermomechanical_runtime import dispatch as chip_glass_thermomechanical_dispatch
from src.api.short_cf_thermomechanical_runtime import dispatch as short_cf_thermomechanical_dispatch
from src.api.perovskite_transport_stability_runtime import dispatch as perovskite_transport_dispatch
from src.api.copper_hotend_local_runtime import dispatch as copper_hotend_local_dispatch
from src.api.aerospace_aluminum_runtime import dispatch as aerospace_aluminum_dispatch

parser=argparse.ArgumentParser(); parser.add_argument("--request",type=Path,required=True); parser.add_argument("--response",type=Path,required=True); args=parser.parse_args()
try:
    request = json.loads(args.request.read_text(encoding="utf-8"))
    domain = (request.get("constraints") or {}).get("model_domain")
    dispatcher = {
        "ni_superalloy_creep_rupture": ni_creep_dispatch,
        "ni_superalloy_hot_end": ni_hotend_dispatch,
        "reusable_rocket_stainless": rocket_stainless_dispatch,
        "chip_glass_cte_family_v1": chip_glass_cte_dispatch,
        "chip_glass_cte_25_300_family_v1": chip_glass_cte_dispatch,
        "chip_glass_thermomechanical_family_v1": chip_glass_thermomechanical_dispatch,
        "short_cf_thermomechanical_rve_v1": short_cf_thermomechanical_dispatch,
        "perovskite_transport_stability_v2": perovskite_transport_dispatch,
        "copper_hot_end_local_composition_v1": copper_hotend_local_dispatch,
        "aerospace_aluminum_composition_v1": aerospace_aluminum_dispatch,
    }.get(domain, hea_dispatch)
    response={"ok":True,"result":dispatcher(request)}
except Exception as exc:
    response={"ok":False,"error":str(exc)}
args.response.write_text(json.dumps(response,ensure_ascii=False),encoding="utf-8")
if not response["ok"]: raise SystemExit(2)
