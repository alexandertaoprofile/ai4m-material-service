"""Independent reader for the existing trusted MP2020-corrected reference LMDB.

The dataset was distributed with MatterGen (MIT); this reader has no MatterGen
imports and does not download/unpack another copy. Pickles are local trusted
administrator-managed data, never user uploads.
"""
from __future__ import annotations

import fcntl
import itertools
import math
import os
import pickle
from pathlib import Path


def phase_diagram(elements):
    import lmdb
    from monty.json import MontyDecoder
    from pymatgen.analysis.phase_diagram import PhaseDiagram
    elements = sorted(set(elements))
    reference = Path(os.getenv("MATTERSIM_REFERENCE_CACHE_DIR", "/data/mattersim_reference_cache")) / "reference_MP2020correction"
    if not reference.is_file():
        raise RuntimeError("Existing MP2020 reference LMDB is missing; no automatic download is permitted")
    cache_root = Path(os.getenv("OMATG_PHASE_DIAGRAM_CACHE_DIR", "/data/se42/omatg_local/runtime/phase_diagrams"))
    cache_root.mkdir(parents=True, exist_ok=True)
    system = "-".join(elements)
    cache = cache_root / (system + ".pickle")
    fingerprint = [reference.stat().st_size, reference.stat().st_mtime_ns]
    with (cache_root / (system + ".lock")).open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if cache.is_file():
            value = pickle.loads(cache.read_bytes())
            if value.get("schema") == 1 and value.get("reference_fingerprint") == fingerprint:
                return value["phase_diagram"]
        entries = []
        decoder = MontyDecoder()
        with lmdb.open(str(reference), subdir=False, readonly=True, lock=False, readahead=False) as database:
            with database.begin() as transaction:
                def get(key, default=None):
                    raw = transaction.get(key.encode("ascii"))
                    return pickle.loads(raw) if raw else default
                for size in range(1, len(elements)+1):
                    for subset in itertools.combinations(elements, size):
                        name = "-".join(subset)
                        for formula in get(f"{name}.reduced_formulas", []):
                            for index in range(get(f"{name}.{formula}.length", 0)):
                                entry = decoder.process_decoded(get(f"{name}.{formula}.{index}"))
                                if entry and math.isfinite(entry.energy):
                                    entries.append(entry)
        if not entries:
            raise ValueError("No reference phases for " + system)
        result = PhaseDiagram(entries)
        staging = cache.with_suffix(f".{os.getpid()}.tmp")
        staging.write_bytes(pickle.dumps({"schema": 1, "reference_fingerprint": fingerprint, "phase_diagram": result}, protocol=pickle.HIGHEST_PROTOCOL))
        os.replace(staging, cache)
        return result


def corrected_entry(structure, energy):
    """Reproduce the existing MP2020 correction assumptions, not a DFT run."""
    from pymatgen.analysis.structure_analyzer import oxide_type
    from pymatgen.entries.compatibility import MaterialsProject2020Compatibility
    from pymatgen.entries.computed_entries import ComputedStructureEntry
    from pymatgen.io.vasp.sets import MPRelaxSet
    import re
    mp_set = MPRelaxSet(structure, user_incar_settings={"KSPACING": 0.5}, user_kpoints_settings=None)
    incar = mp_set.incar
    potcars = ["PBE " + symbol for symbol in mp_set.potcar_symbols]
    u, j = incar.get("LDAUU", []), incar.get("LDAUJ", [])
    if len(j) != len(u):
        j = [0]*len(u)
    hubbards = {re.split("_", symbol)[0]: float(u[index]-j[index]) for index, symbol in enumerate(mp_set.potcar_symbols)} if incar.get("LDAU", False) and len(u) == len(potcars) else {}
    entry = ComputedStructureEntry(structure, float(energy), parameters={"is_hubbard": sum(hubbards.values()) > 1e-8,
        "hubbards": hubbards, "run_type": "GGA+U" if sum(hubbards.values()) > 1e-8 else "GGA", "potcar_symbols": potcars},
        data={"oxide_type": oxide_type(structure), "aspherical": incar.get("LASPH", False)})
    corrected = MaterialsProject2020Compatibility().process_entry(entry)
    if corrected is None:
        raise ValueError("MP2020 correction could not be applied; do not mix uncorrected energy with the reference")
    return corrected
